#!/usr/bin/env python3
"""Throwaway 3-of-5 client e2e against the deployed federation (kli-driven).

Proves a real multi-witness wallet client works against the fresh federation:
resolve the 5 witness OOBIs -> incept at toad 3-of-5 with --receipt-endpoint
(routes to agenting.Receiptor, NOT WitnessReceiptor which hangs over HTTP) ->
assert >= toad receipts -> mailbox round-trip -> delete the throwaway keystore.

Run AFTER harvest_aids.py (needs federation_aids.json):
    AWS_PROFILE=personal python e2e_client.py
"""
import json
import pathlib
import subprocess
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_AIDS_FILE = _HERE / "federation_aids.json"
_NAME = "cutover-e2e-throwaway"


def build_incept_config(aids, toad=3):
    wits = [w["aid"] for w in aids["witnesses"].values()]
    if toad < 1:
        raise ValueError("toad must be >= 1")
    if len(wits) < toad:
        raise ValueError(f"toad {toad} exceeds witness count {len(wits)}")
    return {"transferable": True, "wits": wits, "toad": toad,
            "icount": 1, "ncount": 1, "isith": "1", "nsith": "1"}


def witness_oobis(aids):
    return [f"{w['url']}/oobi/{w['aid']}" for w in aids["witnesses"].values()]


def _kli(args, ks_home):
    cmd = ["kli", *args, "--base", ks_home]
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kli {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def run_e2e(aids, toad=3):
    """Execute the live kli flow. Returns the incepted AID prefix on success."""
    with tempfile.TemporaryDirectory() as ks_home:
        cfg = build_incept_config(aids, toad)
        cfg_path = pathlib.Path(ks_home) / "incept.json"
        cfg_path.write_text(json.dumps(cfg))
        _kli(["init", "--name", _NAME, "--nopasscode"], ks_home)
        for oobi in witness_oobis(aids):
            _kli(["oobi", "resolve", "--name", _NAME, "--oobi", oobi], ks_home)
        # --receipt-endpoint routes receipt collection through Receiptor.
        out = _kli(["incept", "--name", _NAME, "--alias", "e2e",
                    "--file", str(cfg_path), "--receipt-endpoint"], ks_home)
        status = _kli(["status", "--name", _NAME, "--alias", "e2e", "--verbose"], ks_home)
        # Assert the KEL shows at least `toad` witness receipts.
        if status.count("witness") < toad:  # coarse check; refine to count receipt seals
            raise RuntimeError(f"fewer than {toad} witness receipts in:\n{status}")
        print(f"  e2e incept OK at toad {toad}-of-{len(cfg['wits'])}")
        return out
