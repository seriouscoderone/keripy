# -*- encoding: utf-8 -*-
"""
keri.app.lambding module

Setup functions for Lambda-compatible keripy.  Enables running keripy on
AWS Lambda by attaching the same sub-databases that Baser, Keeper, Reger,
Noter and Mailboxer create to DynamoDBer instances.

Key principle
-------------
DynamoDBer already has the same method interface as LMDBer
(putVal, getVal, etc. plus .env.open_db()).  SuberBase only calls
self.db.env.open_db() and self.db.putVal().  So we just attach
sub-database attributes directly to a DynamoDBer instance.

Each ``setup_*`` function lazily imports only the types it needs so the
module stays loadable even when only part of the keri stack is installed.
"""

from __future__ import annotations

import types


# ---------------------------------------------------------------------------
# 1.  Store name constants
#
# Every subkey used by the five setup functions below.  The DynamoDBer
# must be opened with *at least* these store names so that
# env.open_db(key=...) succeeds for each one.
# ---------------------------------------------------------------------------

#  Baser stores (basing.py)
BASER_STORES = [
    "evts.",   "fels.",   "kels.",   "dtss.",   "aess.",
    "sigs.",   "wigs.",   "rcts.",   "ures.",   "vrcs.",
    "vres.",   "pses.",   "pwes.",   "pdes.",   "udes.",
    "uwes.",   "ooes.",   "dels.",   "ldes.",   "qnfs.",
    "fons.",   "migs.",   "vers.",   "esrs.",   "mfes.",
    "dees.",   "stts.",   "wits.",   "habs.",   "names.",
    "sdts.",   "ssgs.",   "scgs.",   "rpys.",   "rpes.",
    "eans.",   "lans.",   "ends.",   "locs.",   "obvs.",
    "witm.",   "gpse.",   "gdee.",   "gdwe.",   "cgms.",
    "epse.",   "epsd.",   "exns.",   "erpy.",   "esigs.",
    "ecigs.",  "epath.",  "essrs.",  "chas.",   "reps.",
    "wkas.",   "kdts.",   "ksns.",   "knas.",   "wwas.",
    "oobis.",  "eoobi.",  "coobi.",  "roobi.",  "woobi.",
    "moobi.",  "mfa.",    "rmfa.",   "schema.", "cfld.",
    "hbys.",   "cons.",   "ccigs.",  "imgs.",   "ifld.",
    "sids.",   "icigs.",  "iimgs.", "dpwe.",   "dune.",
    "dpub.",   "cdel.",   "meids.",  "maids.",
    "fseen.",  # per-(pre,sn) first-seen marker store used by the KERI-layer
               # first-seen gate (Kever) on concurrent backends. PER-WITNESS:
               # never add to SHARED_KEL_STORES.
    # KRAM stores
    "ctyp.",   "msgc.",   "tmsc.",   "pmkm.",   "pmks.",
    "pmsk.",   "trqs.",   "tsgs.",   "ktsg.",   "sscs.",   "ssts.",
    "frcs.",   "tdcs.",   "ptds.",   "bsqs.",   "bsss.",
    "tmqs.",
]

# Public, AID-prefix-keyed key-event / receipt / key-state stores that are SAFE
# to pool into one shared namespace across services in a trust domain (the
# "key-state oracle"). A strict subset of BASER_STORES; excludes the node's hab
# registry, ALL escrows, KRAM/challenge, OOBI queues, and the entire Reger.
# See docs/superpowers/specs/2026-06-15-cdk-kel-oracle-design.md.
SHARED_KEL_STORES = frozenset({
    # KEY-STATE only — the current, self-verifying key state a consumer reads to
    # authenticate a peer: the KEL digest index plus the Kever state record and
    # signed key-state notices. Every witness derives the SAME value for a given
    # AID, so pooling these is idempotent — never a lost write.
    "kels.", "stts.", "ksns.", "knas.",
    # The per-witness KEL/receipt WRITE-logs are deliberately NOT pooled —
    # evts. sigs. wigs. rcts. vrcs. fels. fons. dtss. wits. aess. Each witness owns
    # these in its own namespace; keripy's agenting.Receiptor gathers each
    # witness's own receipt and disseminates it to the others to reach toad
    # (src/keri/app/agenting.py Receiptor.receipt). Pooling wigs. across the pool
    # collapsed all witnesses' receipts to ONE (a witness saw a peer's wig already
    # present and did not contribute its own), so clients could never collect
    # toad-of-N. See the 2026-06-18 SAM->CDK cutover validation.
    # Reachability (end-role / location / endpoint-auth): pooling these makes the
    # oracle REACHABILITY-COMPLETE so a Service-AID resolves an in-domain peer's
    # mailbox/controller endpoint from one local endsFor read (path-(c)). These
    # are public authorization records, NOT confidential — disjoint from
    # NEVER_SHARE_STORES. See 2026-06-17-service-aid-framework-design.md.
    # NOTE: `lans.` (location-auth SAID index) is intentionally NOT pooled: it is
    # a write-side dedup index for /loc/scheme replies, not part of the read path
    # (endsFor/fetchUrls read `locs.`, never `lans.`). The Service-AID only READS
    # the oracle for peer reachability, so pooling `lans.` is unnecessary; pooling
    # it would only matter for an oracle WRITER, which is out of scope here.
    "ends.", "locs.", "eans.",
})

#  Keeper stores (keeping.py)
KEEPER_STORES = [
    "gbls.",  "pris.",  "prxs.",  "nxts.",  "smids.",
    "rmids.", "pres.",  "prms.",  "sits.",  "pubs.",
]

#  Reger stores (vdr/eventing.py + escrowing.py Broker)
REGER_STORES = [
    "tvts.",   "tels.",   "ancs.",   "baks.",   "tibs.",
    "oots",    "twes",    "taes",    "tets.",
    "stts.",   "creds.",  "cancs.",  "ssgs.",   "scgs.",
    "saved.",  "issus.",  "subjs.",  "schms.",
    "mre.",    "mce.",    "mse.",
    # Broker sub-stores under "txn."
    "txn.-dts.", "txn.-sns.", "txn.-sgs.", "txn.-cgs.",
    "txn.-nes",  "txn.-nas.",
    # remaining Reger stores
    "regs.",   "tpwe.",   "tmse.",   "tede.",   "ctel.",
    "cmse.",   "ccrd.",
]

#  Noter stores (notifying.py)
NOTER_STORES = [
    "nots.",  "nidx.",  "ncigs.",
]

#  Mailboxer stores (storing.py)
MAILBOXER_STORES = [
    "tpcs.",  "msgs.",
]

#  Convenience: all stores for a full keripy deployment
ALL_STORES = (
    BASER_STORES
    + KEEPER_STORES
    + REGER_STORES
    + NOTER_STORES
    + MAILBOXER_STORES
)


# ---------------------------------------------------------------------------
# 2.  setup_baser(dber)  --  mirrors Baser.__init__ + Baser.reopen
#     Source: keri/db/basing.py lines 854-1313
# ---------------------------------------------------------------------------

def setup_baser(dber):
    """Attach every sub-database that ``Baser.reopen()`` creates to *dber*.

    Parameters:
        dber (DynamoDBer): An already-opened DynamoDBer instance whose
            ``stores`` list includes all ``BASER_STORES`` entries.

    After this call the *dber* instance carries the same attribute surface
    area as a ``Baser`` (evts, fels, kels, ...).  Higher-level code such as
    ``Habery`` can therefore use *dber* in place of a ``Baser``.
    """
    # Lazy imports to avoid pulling the full keri stack at module load time.
    from ordered_set import OrderedSet as oset

    from ..db.basing import statedict
    from ..db import koming, subing
    from ..core import coring, indexing
    from ..recording import (
        KeyStateRecord, EventSourceRecord,
        HabitatRecord, TopicsRecord,
        OobiRecord, EndpointRecord,
        LocationRecord, ObservedRecord,
        CacheTypeRecord, TxnMsgCacheRecord,
        MsgCacheRecord, WellKnownAuthN,
    )

    # -- Baser.__init__ state (basing.py:872-875) --
    dber.prefixes = oset()
    dber.groups = oset()
    dber._kevers = statedict()
    dber._kevers.db = dber

    # Expose .kevers property-style via a simple attribute (DynamoDBer is not
    # a Baser subclass so we cannot use @property; a plain attribute works
    # because nothing writes to .kevers directly).
    dber.kevers = dber._kevers

    # -- Baser.reopen sub-database declarations (basing.py:918-1310) --

    # Events
    dber.evts = subing.SerderSuber(db=dber, subkey='evts.')
    dber.fels = subing.OnSuber(db=dber, subkey='fels.')
    dber.kels = subing.OnIoDupSuber(db=dber, subkey='kels.')
    dber.dtss = subing.CesrSuber(db=dber, subkey='dtss.', klas=coring.Dater)
    dber.aess = subing.CatCesrSuber(db=dber, subkey='aess.',
                                     klas=(coring.Number, coring.Diger))

    # Signatures
    dber.sigs = subing.CesrIoSetSuber(db=dber, subkey='sigs.',
                                       klas=indexing.Siger)
    dber.wigs = subing.CesrIoSetSuber(db=dber, subkey='wigs.',
                                       klas=indexing.Siger)
    dber.rcts = subing.CatCesrIoSetSuber(db=dber, subkey='rcts.',
                                          klas=(coring.Prefixer, coring.Cigar))
    dber.ures = subing.CatCesrIoSetSuber(db=dber, subkey='ures.',
                                          klas=(coring.Diger, coring.Prefixer,
                                                coring.Cigar))
    dber.vrcs = subing.CatCesrIoSetSuber(db=dber, subkey='vrcs.',
                                          klas=(coring.Prefixer, coring.Number,
                                                coring.Diger, indexing.Siger))
    dber.vres = subing.CatCesrIoSetSuber(db=dber, subkey='vres.',
                                          klas=(coring.Diger, coring.Prefixer,
                                                coring.Number, coring.Diger,
                                                indexing.Siger))

    # Escrows
    dber.pses = subing.OnIoDupSuber(db=dber, subkey='pses.')
    dber.pwes = subing.OnIoDupSuber(db=dber, subkey='pwes.')
    dber.pdes = subing.OnIoDupSuber(db=dber, subkey='pdes.')
    dber.udes = subing.CatCesrSuber(db=dber, subkey='udes.',
                                     klas=(coring.Number, coring.Diger))
    dber.uwes = subing.B64OnIoSetSuber(db=dber, subkey='uwes.')
    dber.ooes = subing.OnIoDupSuber(db=dber, subkey='ooes.')
    dber.dels = subing.OnIoDupSuber(db=dber, subkey='dels.')
    dber.ldes = subing.OnIoDupSuber(db=dber, subkey='ldes.')
    dber.qnfs = subing.IoSetSuber(db=dber, subkey='qnfs.', dupsort=True)

    # First seen ordinals
    dber.fons = subing.CesrSuber(db=dber, subkey='fons.', klas=coring.Number)

    # Migration / version
    dber.migs = subing.CesrSuber(db=dber, subkey='migs.', klas=coring.Dater)
    dber.vers = subing.Suber(db=dber, subkey='vers.')

    # Event source records
    dber.esrs = koming.Komer(db=dber, klas=EventSourceRecord, subkey='esrs.')

    # Misfit escrows
    dber.misfits = subing.OnIoSetSuber(db=dber, subkey='mfes.')

    # Delegable events escrow
    dber.delegables = subing.IoSetSuber(db=dber, subkey='dees.')

    # Key state records
    dber.states = koming.Komer(db=dber, klas=KeyStateRecord, subkey='stts.')

    # Witness prefixes
    dber.wits = subing.CesrIoSetSuber(db=dber, subkey='wits.',
                                       klas=coring.Prefixer)

    # Habitat records
    dber.habs = koming.Komer(db=dber, subkey='habs.', klas=HabitatRecord)
    dber.names = subing.Suber(db=dber, subkey='names.', sep="^")

    # SAD datetime stamps and signatures
    dber.sdts = subing.CesrSuber(db=dber, subkey='sdts.', klas=coring.Dater)
    dber.ssgs = subing.CesrIoSetSuber(db=dber, subkey='ssgs.',
                                       klas=indexing.Siger)
    dber.scgs = subing.CatCesrIoSetSuber(db=dber, subkey='scgs.',
                                          klas=(coring.Verfer, coring.Cigar))

    # Reply messages
    dber.rpys = subing.SerderSuber(db=dber, subkey='rpys.')
    dber.rpes = subing.CesrIoSetSuber(db=dber, subkey='rpes.',
                                       klas=coring.Diger)

    # Endpoint auth
    dber.eans = subing.CesrSuber(db=dber, subkey='eans.', klas=coring.Diger)
    dber.lans = subing.CesrSuber(db=dber, subkey='lans.', klas=coring.Diger)

    # Endpoint / location / observed records
    dber.ends = koming.Komer(db=dber, subkey='ends.', klas=EndpointRecord)
    dber.locs = koming.Komer(db=dber, subkey='locs.', klas=LocationRecord)
    dber.obvs = koming.Komer(db=dber, subkey='obvs.', klas=ObservedRecord)

    # Witness mailbox topic tracking
    dber.tops = koming.Komer(db=dber, subkey='witm.', klas=TopicsRecord)

    # Group partial signature escrow
    dber.gpse = subing.CatCesrIoSetSuber(db=dber, subkey='gpse.',
                                          klas=(coring.Number, coring.Diger))
    # Group delegate escrow
    dber.gdee = subing.CatCesrIoSetSuber(db=dber, subkey='gdee.',
                                          klas=(coring.Number, coring.Diger))
    # Group partial witness escrow
    dber.gpwe = subing.CatCesrIoSetSuber(db=dber, subkey='gdwe.',
                                          klas=(coring.Number, coring.Diger))

    # Completed group multisig
    dber.cgms = subing.CesrSuber(db=dber, subkey='cgms.', klas=coring.Diger)

    # Exchange message partial signature escrow
    dber.epse = subing.SerderSuber(db=dber, subkey='epse.')
    # Exchange message PS escrow datetime
    dber.epsd = subing.CesrSuber(db=dber, subkey='epsd.', klas=coring.Dater)
    # Exchange messages
    dber.exns = subing.SerderSuber(db=dber, subkey='exns.')
    # Forward pointer to provided reply message
    dber.erpy = subing.CesrSuber(db=dber, subkey='erpy.', klas=coring.Saider)

    # Exchange message signatures
    dber.esigs = subing.CesrIoSetSuber(db=dber, subkey='esigs.',
                                        klas=indexing.Siger)
    dber.ecigs = subing.CatCesrIoSetSuber(db=dber, subkey='ecigs.',
                                           klas=(coring.Verfer, coring.Cigar))

    # Exchange pathed attachments
    dber.epath = subing.IoSetSuber(db=dber, subkey='epath.')

    # Exchange message reply text
    dber.essrs = subing.CesrIoSetSuber(db=dber, subkey='essrs.',
                                        klas=coring.Texter)

    # Challenge response
    dber.chas = subing.CesrIoSetSuber(db=dber, subkey='chas.',
                                       klas=coring.Diger)
    dber.reps = subing.CesrIoSetSuber(db=dber, subkey='reps.',
                                       klas=coring.Diger)

    # Well known AuthN
    dber.wkas = koming.IoSetKomer(db=dber, subkey='wkas.', klas=WellKnownAuthN)

    # KSN datetime stamps and key state notices
    dber.kdts = subing.CesrSuber(db=dber, subkey='kdts.', klas=coring.Dater)
    dber.ksns = koming.Komer(db=dber, klas=KeyStateRecord, subkey='ksns.')
    dber.knas = subing.CesrSuber(db=dber, subkey='knas.', klas=coring.Diger)

    # Watcher watched SAID
    dber.wwas = subing.CesrSuber(db=dber, subkey='wwas.', klas=coring.Diger)

    # OOBIs
    dber.oobis = koming.Komer(db=dber, subkey='oobis.', klas=OobiRecord,
                              sep=">")
    dber.eoobi = koming.Komer(db=dber, subkey='eoobi.', klas=OobiRecord,
                              sep=">")
    dber.coobi = koming.Komer(db=dber, subkey='coobi.', klas=OobiRecord,
                              sep=">")
    dber.roobi = koming.Komer(db=dber, subkey='roobi.', klas=OobiRecord,
                              sep=">")
    dber.woobi = koming.Komer(db=dber, subkey='woobi.', klas=OobiRecord,
                              sep=">")
    dber.moobi = koming.Komer(db=dber, subkey='moobi.', klas=OobiRecord,
                              sep=">")
    dber.mfa = koming.Komer(db=dber, subkey='mfa.', klas=OobiRecord,
                            sep=">")
    dber.rmfa = koming.Komer(db=dber, subkey='rmfa.', klas=OobiRecord,
                             sep=">")

    # JSON schema SADs
    dber.schema = subing.SchemerSuber(db=dber, subkey='schema.')

    # Contact field values for remote identifiers
    dber.cfld = subing.Suber(db=dber, subkey='cfld.')

    # Global settings for the Habery environment
    dber.hbys = subing.Suber(db=dber, subkey='hbys.')

    # Signed contact data
    dber.cons = subing.Suber(db=dber, subkey='cons.')

    # Transferable signatures on contact data
    dber.ccigs = subing.CesrSuber(db=dber, subkey='ccigs.', klas=coring.Cigar)

    # Blinded media for contact information (TypeMedia format)
    dber.imgs = subing.CatCesrSuber(db=dber, subkey='imgs.',
                                     klas=(coring.Noncer, coring.Noncer,
                                           coring.Labeler, coring.Texter))

    # Identifier field values for local identifiers
    dber.ifld = subing.Suber(db=dber, subkey='ifld.')

    # Signed identifier data
    dber.sids = subing.Suber(db=dber, subkey='sids.')

    # Transferable signatures on identifier data
    dber.icigs = subing.CesrSuber(db=dber, subkey='icigs.', klas=coring.Cigar)

    # Blinded media for identifier information (TypeMedia format)
    dber.iimgs = subing.CatCesrSuber(db=dber, subkey='iimgs.',
                                      klas=(coring.Noncer, coring.Noncer,
                                            coring.Labeler, coring.Texter))

    # Delegation escrow dbs
    dber.dpwe = subing.SerderSuber(db=dber, subkey='dpwe.')
    dber.dune = subing.SerderSuber(db=dber, subkey='dune.')
    dber.dpub = subing.SerderSuber(db=dber, subkey='dpub.')

    # Completed group delegated AIDs
    dber.cdel = subing.CesrOnSuber(db=dber, subkey='cdel.', klas=coring.Diger)

    # Multisig embed payload SAID -> containing exn messages
    dber.meids = subing.CesrIoSetSuber(db=dber, subkey='meids.',
                                        klas=coring.Diger)
    # Multisig embed payload SAID -> group multisig participant AIDs
    dber.maids = subing.CesrIoSetSuber(db=dber, subkey='maids.',
                                        klas=coring.Prefixer)

    # -- KRAM cache sub-databases --

    # KRAM cache type
    dber.kramCTYP = koming.Komer(db=dber, subkey='ctyp.',
                                  klas=CacheTypeRecord)
    # KRAM message cache
    dber.kramMSGC = koming.Komer(db=dber, subkey='msgc.',
                                  klas=MsgCacheRecord)
    # KRAM transactioned message cache
    dber.kramTMSC = koming.Komer(db=dber, subkey='tmsc.',
                                  klas=TxnMsgCacheRecord)
    # KRAM partially signed multi-key message
    dber.kramPMKM = subing.SerderSuber(db=dber, subkey='pmkm.')
    # KRAM partially signed multi-key signatures
    dber.kramPMKS = subing.CesrIoSetSuber(db=dber, subkey='pmks.',
                                           klas=indexing.Siger)
    # KRAM partially signed multi-key sender key state
    dber.kramPMSK = subing.CatCesrSuber(db=dber, subkey='pmsk.',
                                         klas=(coring.Number, coring.Diger))
    # KRAM trans receipt quadruples
    dber.kramTRQS = subing.CatCesrIoSetSuber(db=dber, subkey='trqs.',
                                              klas=(coring.Prefixer,
                                                    coring.Number,
                                                    coring.Diger,
                                                    indexing.Siger))
    # Upstream Baser db.tsgs: trans sig groups on signed rpy (end-role/loc) replies,
    # used by routing.processReply — the OOBI/end-role path. MUST own 'tsgs.'; its
    # absence here was the 'DynamoDBer has no attribute tsgs' 500 on witness OOBI.
    dber.tsgs = subing.CesrIoSetSuber(db=dber, subkey='tsgs.', klas=indexing.Siger)
    # KRAM trans last sig groups — distinct 'ktsg.' subkey (NOT 'tsgs.', which
    # upstream owns for db.tsgs above; the fork's KRAM repurposing collided).
    dber.kramTSGS = subing.CatCesrIoSetSuber(db=dber, subkey='ktsg.',
                                              klas=(coring.Prefixer,
                                                    coring.Number,
                                                    coring.Diger,
                                                    indexing.Siger))
    # KRAM first seen seal couples
    dber.kramSSCS = subing.CatCesrIoSetSuber(db=dber, subkey='sscs.',
                                              klas=(coring.Number,
                                                    coring.Diger))
    # KRAM source seal triples
    dber.kramSSTS = subing.CatCesrIoSetSuber(db=dber, subkey='ssts.',
                                              klas=(coring.Prefixer,
                                                    coring.Number,
                                                    coring.Diger))
    # KRAM first seen replay couples
    dber.kramFRCS = subing.CatCesrIoSetSuber(db=dber, subkey='frcs.',
                                              klas=(coring.Number,
                                                    coring.Dater))
    # KRAM typed digest seal couples
    dber.kramTDCS = subing.CatCesrIoSetSuber(db=dber, subkey='tdcs.',
                                              klas=(coring.Verser,
                                                    coring.Diger))
    # KRAM pathed streams
    dber.kramPTDS = subing.IoSetSuber(db=dber, subkey='ptds.')

    # KRAM blind state quadruples
    dber.kramBSQS = subing.CatCesrIoSetSuber(db=dber, subkey='bsqs.',
                                              klas=(coring.Diger,
                                                    coring.Noncer,
                                                    coring.Noncer,
                                                    coring.Labeler))
    # KRAM bound state sextuples
    dber.kramBSSS = subing.CatCesrIoSetSuber(db=dber, subkey='bsss.',
                                              klas=(coring.Diger,
                                                    coring.Noncer,
                                                    coring.Noncer,
                                                    coring.Labeler,
                                                    coring.Number,
                                                    coring.Noncer))
    # KRAM type media quadruples
    dber.kramTMQS = subing.CatCesrIoSetSuber(db=dber, subkey='tmqs.',
                                              klas=(coring.Diger,
                                                    coring.Noncer,
                                                    coring.Labeler,
                                                    coring.Texter))

    # -- Bind read-only Baser business methods that operate on the
    #    sub-database attributes attached above.  Without these, callers
    #    using the ``hby.db.method(...)`` pattern (e.g. OOBI handlers
    #    invoking ``fullyWitnessed`` or KEL replay via ``clonePreIter`` /
    #    ``cloneDelegation``) would get AttributeError on a DynamoDBer.
    #    ``reload`` and ``migrate`` are intentionally NOT bound: they
    #    depend on Baser-specific version state and on Lambda the
    #    ``Habery.setup -> loadHabs`` path performs equivalent work.
    import types as _types
    from ..db.basing import Baser
    for _meth in ("fullyWitnessed", "clonePreIter", "cloneAllPreIter",
                  "cloneEvtMsg", "cloneDelegation",
                  "fetchAllSealingEventByEventSeal",
                  "fetchLastSealingEventByEventSeal",
                  "fetchLastSealingEventBySeal",
                  "signingMembers", "rotationMembers",
                  "resolveVerifiers",
                  "getEvtPreIter", "getEvtLastPreIter"):
        setattr(dber, _meth, _types.MethodType(getattr(Baser, _meth), dber))

    # NOTE: We intentionally do NOT call dber.reload().  reload() is a Baser
    # method that iterates .habs and loads kevers from .states.  On Lambda the
    # Habery.setup() -> loadHabs() path handles this instead.

    return dber


def reload_baser(dber):
    """Reload stored prefixes and kevers from ``dber.habs`` / ``dber.states``.

    This mirrors ``Baser.reload()`` (basing.py:1315-1345) but operates on
    a DynamoDBer instance that has been set up via :func:`setup_baser`.

    Call this after ``setup_baser`` when you need kevers populated before
    ``Habery.setup()`` runs.  In most Lambda flows you can skip this and
    let ``Habery.loadHabs()`` populate kevers instead.
    """
    from ..core.eventing import Kever
    from ..kering import MissingEntryError

    removes = []
    for keys, data in dber.habs.getTopItemIter():
        if (ksr := dber.states.get(keys=data.hid)) is not None:
            try:
                kever = Kever(state=ksr, db=dber, local=True)
            except MissingEntryError:
                removes.append(keys)
                continue
            dber.kevers[kever.prefixer.qb64] = kever
            dber.prefixes.add(kever.prefixer.qb64)
            if data.mid:  # group hab
                dber.groups.add(data.hid)
        elif data.mid is None:
            removes.append(keys)

    for keys in removes:
        dber.habs.rem(keys=keys)


# ---------------------------------------------------------------------------
# 3.  setup_keeper(dber)  --  mirrors Keeper.reopen
#     Source: keri/app/keeping.py lines 269-305
# ---------------------------------------------------------------------------

def setup_keeper(dber):
    """Attach every sub-database that ``Keeper.reopen()`` creates to *dber*.

    Parameters:
        dber (DynamoDBer): An already-opened DynamoDBer instance whose
            ``stores`` list includes all ``KEEPER_STORES`` entries.
    """
    from ..core import Prefixer, Number, Cipher
    from ..db import (Suber, CryptSignerSuber, CesrSuber,
                      CatCesrIoSetSuber, Komer)
    from .keeping import PrePrm, PreSit, PubSet

    dber.gbls = Suber(db=dber, subkey='gbls.')
    dber.pris = CryptSignerSuber(db=dber, subkey='pris.')
    dber.prxs = CesrSuber(db=dber, subkey='prxs.', klas=Cipher)
    dber.nxts = CesrSuber(db=dber, subkey='nxts.', klas=Cipher)
    dber.smids = CatCesrIoSetSuber(db=dber, subkey='smids.',
                                    klas=(Prefixer, Number))
    dber.rmids = CatCesrIoSetSuber(db=dber, subkey='rmids.',
                                    klas=(Prefixer, Number))
    dber.pres = CesrSuber(db=dber, subkey='pres.', klas=Prefixer)
    dber.prms = Komer(db=dber, subkey='prms.', klas=PrePrm)
    dber.sits = Komer(db=dber, subkey='sits.', klas=PreSit)
    dber.pubs = Komer(db=dber, subkey='pubs.', klas=PubSet)

    return dber


# ---------------------------------------------------------------------------
# 4.  setup_reger(dber)  --  mirrors Reger.__init__ + Reger.reopen
#     Source: keri/vdr/eventing.py lines 2255-2463
# ---------------------------------------------------------------------------

def setup_reger(dber):
    """Attach every sub-database that ``Reger.reopen()`` creates to *dber*.

    Parameters:
        dber (DynamoDBer): An already-opened DynamoDBer instance whose
            ``stores`` list includes all ``REGER_STORES`` entries.
    """
    from ordered_set import OrderedSet as oset

    from ..vdr.eventing import rbdict
    from ..vdr.vdring import RegistryRecord, RegStateRecord
    from ..core import (Dater, Diger, Number, Prefixer, Saider,
                        Verfer, Cigar, SerderACDC)
    from ..core.indexing import Siger
    from ..db import (Suber, OnSuber, CatCesrSuber, IoDupSuber,
                      CesrDupSuber, OnIoDupSuber, SerderSuber,
                      CesrIoSetSuber, CatCesrIoSetSuber, CesrSuber,
                      Komer, Broker)

    # -- Reger.__init__ state (eventing.py:2353-2356) --
    dber.registries = oset()
    dber._tevers = rbdict()
    dber._tevers.reger = dber
    dber._tevers.db = dber
    dber.tevers = dber._tevers

    # -- Reger.reopen sub-database declarations (eventing.py:2368-2463) --

    dber.tvts = Suber(db=dber, subkey='tvts.')
    dber.tels = OnSuber(db=dber, subkey='tels.')
    dber.ancs = CatCesrSuber(db=dber, subkey='ancs.', klas=(Number, Diger))
    dber.baks = IoDupSuber(db=dber, subkey='baks.')
    dber.tibs = CesrDupSuber(db=dber, subkey='tibs.', klas=Siger)
    dber.oots = OnIoDupSuber(db=dber, subkey='oots')
    dber.twes = OnIoDupSuber(db=dber, subkey='twes')
    dber.taes = OnIoDupSuber(db=dber, subkey='taes')
    dber.tets = CesrSuber(db=dber, subkey='tets.', klas=Dater)

    # Registry state records
    dber.states = Komer(db=dber, klas=RegStateRecord, subkey='stts.')

    # Credential storage
    dber.creds = SerderSuber(db=dber, subkey='creds.', klas=SerderACDC)

    # Anchors to credentials
    dber.cancs = CatCesrSuber(db=dber, subkey='cancs.',
                               klas=(Prefixer, Number, Diger))

    # SAD path indexed signatures
    dber.spsgs = CesrIoSetSuber(db=dber, subkey='ssgs.', klas=Siger)

    # SAD path non-indexed signatures
    dber.spcgs = CatCesrIoSetSuber(db=dber, subkey='scgs.',
                                    klas=(Verfer, Cigar))

    # Credential indices
    dber.saved = CesrSuber(db=dber, subkey='saved.', klas=Saider)
    dber.issus = CesrDupSuber(db=dber, subkey='issus.', klas=Saider)
    dber.subjs = CesrDupSuber(db=dber, subkey='subjs.', klas=Saider)
    dber.schms = CesrDupSuber(db=dber, subkey='schms.', klas=Saider)

    # Escrows
    dber.mre = CesrSuber(db=dber, subkey='mre.', klas=Dater)
    dber.mce = CesrSuber(db=dber, subkey='mce.', klas=Dater)
    dber.mse = CesrSuber(db=dber, subkey='mse.', klas=Dater)

    # Broker: collection of sub-dbs for persisting Registry Txn State Notices
    dber.txnsb = Broker(db=dber, subkey='txn.')

    # Registry keys by name
    dber.regs = Komer(db=dber, subkey='regs.', klas=RegistryRecord)

    # TEL partial witness escrow
    dber.tpwe = CatCesrIoSetSuber(db=dber, subkey='tpwe.',
                                   klas=(Prefixer, Number, Diger))
    # TEL multisig anchor escrow
    dber.tmse = CatCesrIoSetSuber(db=dber, subkey='tmse.',
                                   klas=(Prefixer, Number, Diger))
    # TEL event dissemination escrow
    dber.tede = CatCesrIoSetSuber(db=dber, subkey='tede.',
                                   klas=(Prefixer, Number, Saider))

    # Completed TEL event
    dber.ctel = CesrSuber(db=dber, subkey='ctel.', klas=Saider)

    # Credential Missing Signature Escrow
    dber.cmse = SerderSuber(db=dber, subkey='cmse.', klas=SerderACDC)

    # Completed Credentials
    dber.ccrd = SerderSuber(db=dber, subkey='ccrd.', klas=SerderACDC)

    # -- Bind read-only Reger business methods that operate on the
    #    sub-database attributes attached above (mirrors the setup_baser
    #    binding loop).  Without these, Registrar / Credentialer code using
    #    the ``rgy.reger.method(...)`` pattern (e.g. ``clonePreIter`` in
    #    ``Registrar.processDissemination`` or ``cloneCred`` during IPEX
    #    grant framing) would get AttributeError on a DynamoDBer.
    from ..vdr.eventing import Reger
    for _meth in ("cloneCreds", "logCred", "cloneCred", "clonePreIter",
                  "cloneTvtAt", "cloneTvt", "sources"):
        setattr(dber, _meth, types.MethodType(getattr(Reger, _meth), dber))

    return dber


# ---------------------------------------------------------------------------
# 5.  setup_noter(dber)  --  mirrors Noter.__init__ + Noter.reopen
#     Source: keri/app/notifying.py lines 224-359
# ---------------------------------------------------------------------------

def setup_noter(dber):
    """Attach every sub-database that ``Noter.reopen()`` creates to *dber*,
    plus the business methods (add, update, get, rem, getNoteCnt, getNotes).

    Parameters:
        dber (DynamoDBer): An already-opened DynamoDBer instance whose
            ``stores`` list includes all ``NOTER_STORES`` entries.
    """
    from ..core import Cigar, Diger, MtrDex
    from ..db import CesrSuber, Suber
    from .notifying import DicterSuber, Notice

    dber.notes = DicterSuber(db=dber, subkey='nots.', sep='/', klas=Notice)
    dber.nidx = Suber(db=dber, subkey='nidx.')
    dber.ncigs = CesrSuber(db=dber, subkey='ncigs.', klas=Cigar)

    # -- Bind business methods from Noter (notifying.py:244-359) --

    def _noter_add(self, note, cigar):
        """Add note to database keyed by datetime and SAID of the note."""
        dt = note.datetime
        rid = note.rid
        if self.nidx.get(keys=(rid,)) is not None:
            return False
        self.nidx.pin(keys=(rid,), val=dt.encode())
        self.ncigs.pin(keys=(rid,), val=cigar)
        return self.notes.pin(keys=(dt, rid), val=note)

    def _noter_update(self, note, cigar):
        """Update note in database keyed by datetime and SAID of the note."""
        dt = note.datetime
        rid = note.rid
        if self.nidx.get(keys=(rid,)) is None:
            return False
        self.nidx.pin(keys=(rid,), val=dt.encode())
        self.ncigs.pin(keys=(rid,), val=cigar)
        return self.notes.pin(keys=(dt, rid), val=note)

    def _noter_get(self, rid):
        """Get note and its signature by random ID."""
        dt = self.nidx.get(keys=(rid,))
        if dt is None:
            return None
        note = self.notes.get(keys=(dt, rid))
        cig = self.ncigs.get(keys=(rid,))
        return note, cig

    def _noter_rem(self, rid):
        """Remove note from database if it exists."""
        res = self.get(rid)
        if res is None:
            return False
        note, _ = res
        dt = note.datetime
        rid = note.rid
        self.nidx.rem(keys=(rid,))
        self.ncigs.rem(keys=(rid,))
        return self.notes.rem(keys=(dt, rid))

    def _noter_getNoteCnt(self):
        """Return count of all notes."""
        return self.notes.cntAll()

    def _noter_getNotes(self, start=0, end=25):
        """Return list of (note, cigar) tuples.

        Parameters:
            start (int): number of item to start at
            end (int): number of last item to return (-1 for all)
        """
        if hasattr(start, "isoformat"):
            start = start.isoformat()

        notes = []
        it = self.notes.getTopItemIter(keys=())

        # Skip items before start
        for _ in range(start):
            try:
                next(it)
            except StopIteration:
                break

        for ((_, _), note) in it:
            cig = self.ncigs.get(keys=(note.rid,))
            notes.append((note, cig))
            if (not end == -1) and len(notes) == (end - start) + 1:
                break

        return notes

    dber.add = types.MethodType(_noter_add, dber)
    dber.update = types.MethodType(_noter_update, dber)
    dber.get = types.MethodType(_noter_get, dber)
    dber.rem = types.MethodType(_noter_rem, dber)
    dber.getNoteCnt = types.MethodType(_noter_getNoteCnt, dber)
    dber.getNotes = types.MethodType(_noter_getNotes, dber)

    return dber


# ---------------------------------------------------------------------------
# 6.  setup_mailboxer(dber)  --  mirrors Mailboxer.__init__ + Mailboxer.reopen
#     Source: keri/app/storing.py lines 19-154
# ---------------------------------------------------------------------------

def setup_mailboxer(dber):
    """Attach every sub-database that ``Mailboxer.reopen()`` creates to *dber*,
    plus the business methods (delTopic, appendToTopic, getTopicMsgs,
    storeMsg, cloneTopicIter).

    Parameters:
        dber (DynamoDBer): An already-opened DynamoDBer instance whose
            ``stores`` list includes all ``MAILBOXER_STORES`` entries.
    """
    from ..core import Diger, MtrDex
    from ..db import OnSuber, Suber

    dber.tpcs = OnSuber(db=dber, subkey='tpcs.')
    dber.msgs = Suber(db=dber, subkey='msgs.')

    # -- Bind business methods from Mailboxer (storing.py:65-154) --

    def _mbx_delTopic(self, key, on=0):
        """Remove topic index from .tpcs without deleting message from .msgs."""
        return self.tpcs.rem(keys=key, on=on)

    def _mbx_appendToTopic(self, topic, val):
        """Append val to end of db entries with same topic, on incremented."""
        return self.tpcs.append(key=topic, val=val)

    def _mbx_getTopicMsgs(self, topic, fn=0):
        """Return messages belonging to topic indices on >= fn."""
        msgs = []
        for keys, on, dig in self.tpcs.getAllItemIter(keys=topic, on=fn):
            if msg := self.msgs.get(keys=dig):
                msgs.append(msg.encode())  # want bytes not str
        return msgs

    def _mbx_storeMsg(self, topic, msg):
        """Add exn event to mailbox topic, 1 greater than last msg at topic."""
        if hasattr(msg, "encode"):
            msg = msg.encode("utf-8")
        digb = Diger(ser=msg, code=MtrDex.Blake3_256).qb64b
        on = self.tpcs.append(keys=topic, val=digb)
        return self.msgs.pin(keys=digb, val=msg)

    def _mbx_cloneTopicIter(self, topic, fn=0):
        """Yield (on, topic, msg) triples starting at ordinal fn."""
        for keys, on, dig in self.tpcs.getAllItemIter(keys=topic, on=fn):
            if msg := self.msgs.get(keys=dig):
                yield (on, topic, msg.encode("utf-8"))

    dber.delTopic = types.MethodType(_mbx_delTopic, dber)
    dber.appendToTopic = types.MethodType(_mbx_appendToTopic, dber)
    dber.getTopicMsgs = types.MethodType(_mbx_getTopicMsgs, dber)
    dber.storeMsg = types.MethodType(_mbx_storeMsg, dber)
    dber.cloneTopicIter = types.MethodType(_mbx_cloneTopicIter, dber)

    return dber


# ---------------------------------------------------------------------------
# 7.  handler() and init()  --  Lambda entry point
#
# Cold-start initialisation pattern.  The first invocation opens a
# DynamoDBer with BASER_STORES + KEEPER_STORES, runs setup_baser and
# setup_keeper, then builds a Habery wired to those databases.
# Subsequent invocations reuse the module-level singletons.
# ---------------------------------------------------------------------------

_hby = None   # module-level Habery singleton (warm across invocations)
_db = None    # module-level baser DynamoDBer
_ks = None    # module-level keeper DynamoDBer


def init(*, name="lambda", baser_table=None, keeper_table=None,
         region="us-east-1", endpoint_url=None, salt=None, **kwa):
    """Cold-start initialisation: open DynamoDBer instances, attach
    sub-databases, and create a ``Habery``.

    Parameters:
        name (str): Base name for the keri databases.
        baser_table (str | None): DynamoDB table for Baser data.
            Defaults to ``keri-{name}``.
        keeper_table (str | None): DynamoDB table for Keeper data.
            Defaults to ``keri-{name}-ks``.
        region (str): AWS region.
        endpoint_url (str | None): Override endpoint (DynamoDB Local).
        salt (str | None): qb64-encoded salt for key-pair creation.
        **kwa: Extra keyword arguments forwarded to ``Habery.__init__``.

    Returns:
        Habery: A fully initialised Habery instance.
    """
    global _hby, _db, _ks

    from ..db.dynamodbing import DynamoDBer
    from .habbing import Habery

    if baser_table is None:
        baser_table = f"keri-{name}"
    if keeper_table is None:
        keeper_table = f"keri-{name}-ks"

    dynamo_kwa = dict(region=region)
    if endpoint_url:
        dynamo_kwa["endpoint_url"] = endpoint_url

    # Open DynamoDBer for Baser stores
    _db = DynamoDBer.open(
        name=name,
        stores=BASER_STORES,
        table_name=baser_table,
        **dynamo_kwa,
    )
    setup_baser(_db)

    # Open DynamoDBer for Keeper stores
    _ks = DynamoDBer.open(
        name=f"{name}-ks",
        stores=KEEPER_STORES,
        table_name=keeper_table,
        **dynamo_kwa,
    )
    setup_keeper(_ks)

    # Build Habery, injecting our DynamoDBer instances as db and ks.
    # Habery.__init__ normally creates Baser / Keeper internally; we
    # bypass that by passing pre-built db and ks.
    _hby = Habery(name=name, db=_db, ks=_ks, salt=salt, **kwa)

    return _hby


def handler(event, context):
    """AWS Lambda entry point.

    On cold start, calls :func:`init` to set up the Habery.
    On warm start, reuses the module-level ``_hby`` singleton.

    Environment variables consumed:
        KERI_NAME        -- base name (default ``"lambda"``)
        KERI_BASER_TABLE -- DynamoDB table for Baser data
        KERI_KEEPER_TABLE -- DynamoDB table for Keeper data
        KERI_REGION      -- AWS region (default ``"us-east-1"``)
        KERI_ENDPOINT_URL -- DynamoDB endpoint override (for local dev)
        KERI_SALT        -- qb64-encoded salt

    Parameters:
        event (dict): Lambda event payload.
        context (LambdaContext): Lambda runtime context.

    Returns:
        dict: Response payload.  Subclass or replace this function to
            implement your application logic.
    """
    import os

    global _hby

    if _hby is None:
        init(
            name=os.environ.get("KERI_NAME", "lambda"),
            baser_table=os.environ.get("KERI_BASER_TABLE"),
            keeper_table=os.environ.get("KERI_KEEPER_TABLE"),
            region=os.environ.get("KERI_REGION", "us-east-1"),
            endpoint_url=os.environ.get("KERI_ENDPOINT_URL"),
            salt=os.environ.get("KERI_SALT"),
        )

    import json
    import base64

    path = event.get("path", event.get("resource", "/"))
    method = event.get("httpMethod", "GET")

    # GET /keri — status + key state
    if method == "GET":
        habs_info = []
        for pre, hab in _hby.habs.items():
            habs_info.append({
                "pre": pre,
                "name": hab.name,
                "sn": hab.kever.sn,
                "key": hab.kever.verfers[0].qb64,
            })
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "message": "keripy Lambda ready",
                "name": _hby.name,
                "habs": habs_info,
            }),
        }

    # POST /keri — process CESR message
    if method == "POST":
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body)
        elif isinstance(body, str):
            body = body.encode("utf-8")

        if not body:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "empty body"}),
            }

        # Parse CESR message through Kevery
        ims = bytearray(body)
        _hby.psr.parse(ims=ims)

        # Process any escrows this message may have unblocked
        _hby.kvy.processEscrows()

        # Drain cues
        cues = []
        while _hby.kvy.cues:
            cue = _hby.kvy.cues.popleft()
            cues.append({"kin": cue.get("kin", "unknown")})

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "processed": True,
                "cues": cues,
                "kevers": len(_hby.kevers),
            }),
        }

    return {
        "statusCode": 405,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "method not allowed"}),
    }
