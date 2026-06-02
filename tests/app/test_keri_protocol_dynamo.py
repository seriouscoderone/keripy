# -*- encoding: utf-8 -*-
"""
tests.app.test_keri_protocol_dynamo module

Full KERI protocol tests running entirely on DynamoDB backend.
No LMDB anywhere — proves keripy can operate serverlessly.

Tests:
  1. Inception — create identifiers for controller and validator
  2. Receipt exchange — validator receipts controller's inception
  3. Rotation — controller rotates keys, validator receipts rotation
  4. Interaction — controller creates interaction event
  5. Hab-level — full Habery lifecycle (makeHab, rotate, interact)
"""

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

try:
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import (
        BASER_STORES, KEEPER_STORES,
        setup_baser, setup_keeper,
    )
    HAS_LAMBDING = True
except ImportError:
    HAS_LAMBDING = False

needs = pytest.mark.skipif(
    not (HAS_MOTO and HAS_LAMBDING),
    reason="requires moto and keri.app.lambding",
)


def _open_db(name, stores):
    """Open a DynamoDBer and return it."""
    return DynamoDBer.open(name=name, stores=stores, region="us-east-1")


def _make_baser(name):
    """Open a DynamoDBer, attach Baser sub-databases, return it."""
    dber = _open_db(f"{name}-db", BASER_STORES)
    setup_baser(dber)
    return dber


def _make_keeper(name):
    """Open a DynamoDBer, attach Keeper sub-databases, return it."""
    dber = _open_db(f"{name}-ks", KEEPER_STORES)
    setup_keeper(dber)
    return dber


@needs
class TestDirectModeOnDynamo:
    """
    Port of test_direct_mode_with_manager from tests/comply/test_direct_mode.py
    but running entirely on DynamoDB (via moto).

    Controller (coe) and Validator (val) exchange:
      1. Inception events
      2. Receipts of each other's inception
      3. Controller rotates keys
      4. Validator receipts rotation
      5. Controller creates interaction event
    """

    def test_full_direct_mode(self):
        from keri.kering import Vrsn_1_0
        from keri.core import (Kevery, Parser, MtrDex, SealEvent,
                               incept, rotate, interact, messagize, receipt)
        from keri.core.signing import Salter
        from keri.app.keeping import Manager
        from keri.db import dgKey, snKey

        with mock_aws():
            coeDB = _make_baser("coe")
            coeKS = _make_keeper("coe")
            valDB = _make_baser("val")
            valKS = _make_keeper("val")

            # --- Setup key managers ---
            coeSalt = Salter(raw=b'0123456789abcdea').qb64
            valSalt = Salter(raw=b'1123456789abcdea').qb64

            coeMgr = Manager(ks=coeKS, salt=coeSalt)
            coeVerfers, coeDigers = coeMgr.incept(icount=1, ncount=1)

            valMgr = Manager(ks=valKS, salt=valSalt)
            valVerfers, valDigers = valMgr.incept(icount=1, ncount=1)

            # --- Setup Keverys ---
            coeKevery = Kevery(db=coeDB)
            valKevery = Kevery(db=valDB)

            csn = 0  # controller sequence number

            # ========================================
            # STEP 1: Controller inception
            # ========================================
            coeSerder = incept(keys=[coeVerfers[0].qb64],
                               ndigs=[coeDigers[0].qb64],
                               code=MtrDex.Blake3_256)
            coepre = coeSerder.ked["i"]
            sigers = coeMgr.sign(ser=coeSerder.raw, verfers=coeVerfers)
            cmsg = messagize(coeSerder, sigers=sigers)

            # Controller processes own inception
            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(cmsg), kvy=coeKevery)
            coeKever = coeKevery.kevers[coepre]
            assert coeKever.prefixer.qb64 == coepre
            assert coeKever.sn == 0
            print(f"[COE] Inception: pre={coepre[:20]}...")

            # ========================================
            # STEP 2: Validator inception
            # ========================================
            valSerder = incept(keys=[valVerfers[0].qb64],
                               ndigs=[valDigers[0].qb64],
                               code=MtrDex.Blake3_256)
            valpre = valSerder.ked["i"]
            sigers = valMgr.sign(valSerder.raw, verfers=valVerfers)
            vmsg = messagize(valSerder, sigers=sigers)

            # Validator processes own inception
            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(vmsg), kvy=valKevery)
            valKever = valKevery.kevers[valpre]
            assert valKever.prefixer.qb64 == valpre
            print(f"[VAL] Inception: pre={valpre[:20]}...")

            # ========================================
            # STEP 3: Validator receives controller's inception
            # ========================================
            Parser(version=Vrsn_1_0).parse(ims=bytearray(cmsg), kvy=valKevery)
            assert coepre in valKevery.kevers
            print(f"[VAL] Received COE inception, now in kevers")

            # ========================================
            # STEP 4: Validator creates receipt of controller's inception
            # ========================================
            seal = SealEvent(i=valpre,
                             s="{:x}".format(valKever.lastEst.s),
                             d=valKever.lastEst.d)
            coeK = valKevery.kevers[coepre]
            reserder = receipt(pre=coeK.prefixer.qb64,
                               sn=coeK.sn,
                               said=coeK.serder.said)

            # Sign the controller's event (not the receipt)
            coeIcpDig = valKevery.db.kels.getLast(keys=coepre, on=csn)
            coeIcpDig = coeIcpDig.encode("utf-8")
            s = valKevery.db.evts.get(keys=(coepre, coeIcpDig))
            sigers = valMgr.sign(ser=s.raw, verfers=valVerfers)
            rmsg = messagize(reserder, sigers=sigers, seal=seal)

            # Validator processes own receipt
            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(rmsg), kvy=valKevery)

            # Simulate sending val's inception + receipt to controller
            vmsg_combined = bytearray(vmsg)
            vmsg_combined.extend(rmsg)
            Parser(version=Vrsn_1_0).parse(ims=vmsg_combined, kvy=coeKevery)

            # Controller now knows validator
            assert valpre in coeKevery.kevers
            # Controller has receipt from validator in its receipt database
            result = coeKevery.db.vrcs.get(keys=dgKey(pre=coeKever.prefixer.qb64,
                                                      dig=coeKever.serder.said))
            assert len(result) > 0
            val_prefixer, est_num, est_diger, sig = result[0]
            assert val_prefixer.qb64 == valKever.prefixer.qb64
            print(f"[COE] Received VAL receipt of inception, verified in vrcs db")

            # ========================================
            # STEP 5: Controller sends receipt of validator's inception
            # ========================================
            seal = SealEvent(i=coepre,
                             s="{:x}".format(coeKever.lastEst.s),
                             d=coeKever.lastEst.d)
            valK = coeKevery.kevers[valpre]
            reserder = receipt(pre=valK.prefixer.qb64,
                               sn=valK.sn,
                               said=valK.serder.said)
            valIcpDig = coeKevery.db.kels.getLast(keys=valpre, on=0)
            valIcpDig = valIcpDig.encode("utf-8")
            s = coeKevery.db.evts.get(keys=(valpre, valIcpDig))
            sigers = coeMgr.sign(ser=s.raw, verfers=coeVerfers)
            cmsg_rct = messagize(reserder, sigers=sigers, seal=seal)

            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(cmsg_rct), kvy=coeKevery)
            Parser(version=Vrsn_1_0).parse(ims=cmsg_rct, kvy=valKevery)

            result = valKevery.db.vrcs.get(keys=dgKey(pre=valKever.prefixer.qb64,
                                                      dig=valKever.serder.said))
            assert len(result) > 0
            print(f"[VAL] Received COE receipt of inception, verified in vrcs db")

            # ========================================
            # STEP 6: Controller rotates keys
            # ========================================
            csn += 1
            coeVerfers, coeDigers = coeMgr.rotate(pre=coeVerfers[0].qb64)
            coeSerder = rotate(pre=coeKever.prefixer.qb64,
                               keys=[coeVerfers[0].qb64],
                               dig=coeKever.serder.said,
                               ndigs=[coeDigers[0].qb64],
                               sn=csn)
            sigers = coeMgr.sign(coeSerder.raw, verfers=coeVerfers)
            cmsg = messagize(coeSerder, sigers=sigers)

            # Controller processes own rotation
            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(cmsg), kvy=coeKevery)
            assert coeKever.sn == 1
            print(f"[COE] Rotation: sn={coeKever.sn}, new key={coeVerfers[0].qb64[:20]}...")

            # Validator receives rotation
            Parser(version=Vrsn_1_0).parse(ims=cmsg, kvy=valKevery)
            assert coeK.sn == 1
            print(f"[VAL] Received COE rotation, sn={coeK.sn}")

            # ========================================
            # STEP 7: Validator receipts rotation
            # ========================================
            seal = SealEvent(i=valpre,
                             s="{:x}".format(valKever.lastEst.s),
                             d=valKever.lastEst.d)
            reserder = receipt(pre=coeK.prefixer.qb64,
                               sn=coeK.sn,
                               said=coeK.serder.said)
            coeRotDig = valKevery.db.kels.getLast(keys=coepre, on=csn)
            coeRotDig = coeRotDig.encode("utf-8")
            s = valKevery.db.evts.get(keys=(coepre, coeRotDig))
            sigers = valMgr.sign(ser=s.raw, verfers=valVerfers)
            vmsg = messagize(reserder, sigers=sigers, seal=seal)

            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(vmsg), kvy=valKevery)
            Parser(version=Vrsn_1_0).parse(ims=vmsg, kvy=coeKevery)

            result = coeKevery.db.vrcs.get(keys=dgKey(pre=coeKever.prefixer.qb64,
                                                      dig=coeKever.serder.said))
            assert len(result) > 0
            print(f"[COE] Received VAL receipt of rotation, verified")

            # ========================================
            # STEP 8: Controller interaction event
            # ========================================
            csn += 1
            coeSerder = interact(pre=coeKever.prefixer.qb64,
                                 dig=coeKever.serder.said,
                                 sn=csn)
            sigers = coeMgr.sign(coeSerder.raw, verfers=coeVerfers)
            cmsg = messagize(coeSerder, sigers=sigers)

            Parser(version=Vrsn_1_0).parseOne(ims=bytearray(cmsg), kvy=coeKevery)
            assert coeKever.sn == 2
            print(f"[COE] Interaction: sn={coeKever.sn}")

            # Validator receives interaction
            Parser(version=Vrsn_1_0).parse(ims=cmsg, kvy=valKevery)
            assert coeK.sn == 2
            print(f"[VAL] Received COE interaction, sn={coeK.sn}")

            # ========================================
            # VERIFY: Full KEL integrity in DynamoDB
            # ========================================
            # Controller's KEL: verify each event exists at correct sn
            for sn in range(3):
                dig = coeDB.kels.getLast(keys=coepre, on=sn)
                assert dig is not None, f"Missing KEL entry at sn={sn}"
            print(f"[DB] Controller KEL: 3 events verified in DynamoDB (sn=0,1,2)")

            # Validator has copy of controller's KEL
            for sn in range(3):
                dig = valDB.kels.getLast(keys=coepre, on=sn)
                assert dig is not None, f"Validator missing KEL entry at sn={sn}"
            print(f"[DB] Validator's copy of Controller KEL: 3 events verified")

            # Receipts stored
            rct_count = coeDB.vrcs.cntAll()
            assert rct_count >= 2  # at least inception + rotation receipts
            print(f"[DB] Controller has {rct_count} verified receipt(s)")

            print("\n=== ALL KERI PROTOCOL TESTS PASSED ON DYNAMODB ===")

            coeDB.close()
            coeKS.close()
            valDB.close()
            valKS.close()


@needs
class TestHabLevelOnDynamo:
    """Test high-level Hab operations on DynamoDB."""

    def test_hab_inception_rotation_interaction(self):
        """Full Hab lifecycle: inception, rotation, interaction — all on DynamoDB."""
        from keri.app.habbing import Habery
        from keri.core.signing import Salter

        with mock_aws():
            db = _make_baser("hab")
            ks = _make_keeper("hab")

            salt = Salter(raw=b'0123456789abcdef').qb64
            hby = Habery(name="test", temp=False, free=True, db=db, ks=ks, salt=salt)

            # --- Inception ---
            hab = hby.makeHab(name="alice", icount=1, isith="1",
                              ncount=1, nsith="1", transferable=True)
            assert hab.pre is not None
            assert hab.kever.sn == 0
            orig_key = hab.kever.verfers[0].qb64
            print(f"[HAB] Inception: pre={hab.pre[:20]}..., key={orig_key[:20]}...")

            # --- Rotation ---
            hab.rotate()
            assert hab.kever.sn == 1
            new_key = hab.kever.verfers[0].qb64
            assert new_key != orig_key
            print(f"[HAB] Rotation: sn=1, new key={new_key[:20]}...")

            # --- Interaction ---
            hab.interact()
            assert hab.kever.sn == 2
            print(f"[HAB] Interaction: sn=2")

            # --- Sign and verify ---
            data = b"hello KERI on Lambda"
            sigers = hab.sign(ser=data, verfers=hab.kever.verfers, indexed=True)
            assert len(sigers) == 1
            # Verify signature
            assert sigers[0].verfer.verify(sig=sigers[0].raw, ser=data)
            print(f"[HAB] Sign+Verify: OK")

            # --- Verify KEL in DynamoDB ---
            for sn in range(3):
                dig = db.kels.getLast(keys=hab.pre, on=sn)
                assert dig is not None, f"Missing KEL entry at sn={sn}"
            state = db.states.get(keys=(hab.pre,))
            assert state.s == "2"  # sn=2 after ixn
            print(f"[DB] KEL: 3 events verified, state.s={state.s}")

            print("\n=== HAB LIFECYCLE ON DYNAMODB PASSED ===")

            hby.close()
