# layer_smoke — real-AWS proof of the zip+layer runtime model

This probe answers the make-or-break question for the whole CDK Phase B runtime
model: **can a pure-Python *zip* Lambda, riding the prebuilt arm64
`KeriRuntimeLayer`, load libsodium and actually sign?** If libsodium does not
resolve, keripy cannot incept the witness AID and nothing downstream works.

It cannot be answered by a mock — moto / DynamoDB-Local never exercise the
native libsodium load path on the real Lambda arm64 runtime. So it runs against
**real AWS** (us-east-1, account `117870855864`) with everything thrown away.

## Run

```bash
AWS_PROFILE=personal .venv/bin/python keri_cdk/probes/layer_smoke/probe.py --region us-east-1
# leave resources to inspect:  --keep
# clean up a kept run:         --teardown-only --suffix <suffix>
```

The layer asset must be built first:

```bash
bash keri_cdk/layers/build_layer.sh
```

## What proved out (real run, 2026-06-13)

```
witness AID returned (libsodium signed inception): BDPtmrt1JUo_gMZY63FrqV6BX0TmZhafSVGV9dJu5FGd
OOBI served 200 + CESR (signing + serving)       : YES
AWS leftovers (keri-layer-smoke-*)               : NONE
VERDICT: PASS
```

The OOBI body was a real signed CESR inception stream
(`{"v":"KERI10JSON0000fd_","t":"icp",...}`), and the AID is a `B`-prefix
non-transferable key — both only producible if libsodium signed.

## The libsodium placement that made it resolve from `/opt/lib`

A Lambda **layer** zip extracts to `/opt`. The asset is laid out so that:

| asset path (`keri_cdk/layers/keri_runtime/`) | extracts to | role |
| --- | --- | --- |
| `python/` (keri + all pip deps) | `/opt/python` | on `sys.path` |
| `lib/libsodium.so.26.1.0` | `/opt/lib/libsodium.so.26.1.0` | the real ELF |
| `lib/libsodium.so.26` → `libsodium.so.26.1.0` | `/opt/lib/libsodium.so.26` | SONAME symlink |

- The `.so` is a genuine **ARM aarch64** ELF (`dnf install libsodium` inside
  `public.ecr.aws/lambda/python:3.14-arm64`; it lands in `/usr/lib64`). The
  build script copies it **and the SONAME symlink** (`cp -P`), and the probe's
  zipper preserves the symlink (zip unix symlink external-attrs) so the SONAME
  the loader looks for actually exists in `/opt/lib`.
- The function sets **`LD_LIBRARY_PATH=/opt/lib`**.
- pysodium loads via `ctypes.cdll.LoadLibrary(ctypes.util.find_library('sodium'))`.
  On the Amazon Linux Lambda image there is **no gcc/ldconfig** for
  `find_library` to consult, so a SONAME-only lookup returns `None` and the load
  fails. The fix is `bootstrap.ensure_libsodium()` (called at the top of
  `witness_handler.py`, before any keri import): it finds the absolute path —
  searching `<code>/lib`, every `LD_LIBRARY_PATH` dir, then `/opt/lib` — and
  monkeypatches `ctypes.util.find_library('sodium')` to return that absolute
  path. So libsodium resolves from **`/opt/lib/libsodium.so.26`**.

## Runtime note: python3.14, not python3.13

keripy's `setup.py` pins `python_requires='>=3.14.0'`, so `python3.13` cannot
even `pip install keri`. The layer, the function, and the build image therefore
all target **`python3.14`** (GA in Lambda since Nov 2025, supported by CDK
2.259.0). The plan was drafted assuming 3.13; this is the one corrected
deviation.

## Layer size

- compressed layer zip published to Lambda: **~18.5 MB**
- unzipped asset (`du -sh keri_cdk/layers/keri_runtime`): **62 MB**

Well under Lambda's 250 MB unzipped layer+function limit.
