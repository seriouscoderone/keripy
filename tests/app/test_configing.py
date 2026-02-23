# -*- encoding: utf-8 -*-
"""
tests.app.configin module
"""
import os
import platform
import shutil
import tempfile

import pytest

from hio.base import doing
from keri.app import configing
from keri.core import coring

def test_configer():
    """
    Test Configer class
    """
    # Test Filer with file not dir
    tempDirPath = os.path.join(os.path.sep, "tmp") if platform.system() == "Darwin" else tempfile.gettempdir()
    filepath = os.path.join(os.path.sep, 'usr', 'local', 'var', 'keri', 'cf', 'main', 'conf.json')
    if os.path.exists(filepath):
        os.remove(filepath)

    cfr = configing.Configer()  # defaults
    # assert cfr.path == filepath
    # github runner does not allow /usr/local/var
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert cfr.opened
    assert os.path.exists(cfr.path)
    assert cfr.file
    assert not cfr.file.closed
    assert not cfr.file.read()
    assert cfr.human

    # plain json manually
    data = dict(name="habi", oobi="ABCDEFG")
    wmsg = coring.dumps(data)
    assert hasattr(wmsg, "decode")  # bytes
    assert len(wmsg) == cfr.file.write(wmsg)
    assert 0 == cfr.file.seek(0)
    rmsg = cfr.file.read()
    assert rmsg == wmsg
    assert data == coring.loads(rmsg)

     # default is hjson for .human == True
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata
    assert 0 == cfr.file.seek(0)
    rmsg = cfr.file.read()
    assert rmsg == b'{\n  name: hope\n  oobi: abc\n}'  # hjson

    cfr.close()
    assert not cfr.opened
    assert cfr.file.closed
    # assert cfr.path == filepath
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    with pytest.raises(ValueError):
        rdata = cfr.get()

    cfr.reopen(reuse=True)  # reuse True and clear False so don't remake
    assert cfr.opened
    assert not cfr.file.closed
    # assert cfr.path == filepath
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == wdata  # not empty

    cfr.reopen()  # reuse False so remake but not clear
    assert cfr.opened
    assert not cfr.file.closed
    # assert cfr.path == filepath
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == wdata  # not empty

    cfr.reopen(reuse=True, clear=True)  # clear True so remake even if reuse
    assert cfr.opened
    assert not cfr.file.closed
    # assert cfr.path == filepath
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == {}  # empty
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata

    cfr.reopen(clear=True)  # clear True so remake
    assert cfr.opened
    assert not cfr.file.closed
    # assert cfr.path == filepath
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == {}  # empty
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata

    cfr.close(clear=True)
    assert not os.path.exists(cfr.path)
    with pytest.raises(ValueError):
        rdata = cfr.get()

    # Test with plain json human==False
    cfr = configing.Configer(human=False)
    # assert cfr.path == filepath
    # github runner does not allow /usr/local/var
    assert cfr.path.endswith(os.path.join('keri', 'cf', 'main', 'conf.json'))
    assert cfr.opened
    assert os.path.exists(cfr.path)
    assert cfr.file
    assert not cfr.human
    assert not cfr.file.closed
    assert not cfr.file.read()

    #  .human == False
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata
    assert 0 == cfr.file.seek(0)
    rmsg = cfr.file.read()
    assert rmsg == b'{\n  "name": "hope",\n  "oobi": "abc"\n}'  # plain json
    cfr.close(clear=True)
    assert not os.path.exists(cfr.path)

    # Test with altPath by using not permitted headDirPath /opt/keri to force Alt
    filepath = os.path.join(os.path.sep, cfr.AltHeadDirPath, cfr.AltTailDirPath, "main", "conf.json")
    if os.path.exists(filepath):
        os.remove(filepath)

    headDirPath = "/root/keri"
    if platform.system() == "Windows":
        headDirPath="C:\\System Volume Information"
    cfr = configing.Configer(headDirPath=headDirPath)
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))
    assert cfr.opened
    assert os.path.exists(cfr.path)
    print(cfr.path)
    assert cfr.file
    assert not cfr.file.closed
    assert not cfr.file.read()

    data = dict(name="habi", oobi="ABCDEFG")
    wmsg = coring.dumps(data)
    assert hasattr(wmsg, "decode")  # bytes
    assert len(wmsg) == cfr.file.write(wmsg)
    assert 0 == cfr.file.seek(0)
    rmsg = cfr.file.read()
    assert rmsg == wmsg
    assert data == coring.loads(rmsg)

    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata

    cfr.close()
    assert not cfr.opened
    assert cfr.file.closed
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    with pytest.raises(ValueError):
        rdata = cfr.get()

    cfr.reopen(reuse=True)  # reuse True and clear False so don't remake
    assert cfr.opened
    assert not cfr.file.closed
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == wdata  # not empty

    cfr.reopen()  # reuse False so remake but not clear
    assert cfr.opened
    assert not cfr.file.closed
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == wdata  # not empty

    if platform.system() == "Windows":
        cfr.reopen(reuse=True, clear=True, headDirPath="C:\\System Volume Information")  # clear True so remake even if reuse
    else:
        cfr.reopen(reuse=True, clear=True)
    assert cfr.opened
    assert not cfr.file.closed
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))

    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == {}  # empty
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata

    cfr.reopen(clear=True)  # clear True so remake
    assert cfr.opened
    assert not cfr.file.closed
    assert cfr.path.endswith(os.path.join('.keri', 'cf', 'main', 'conf.json'))
    assert os.path.exists(cfr.path)
    assert (rdata := cfr.get()) == {}  # empty
    wdata = dict(name="hope", oobi="abc")
    assert cfr.put(wdata)
    rdata = cfr.get()
    assert rdata == wdata

    cfr.close(clear=True)
    assert not os.path.exists(cfr.path)
    with pytest.raises(ValueError):
        rdata = cfr.get()

    #test openCF hjson
    with configing.openCF() as cfr:  # default uses json and temp==True
        filepath = os.path.join(tempDirPath, 'keri_cf_2_zu01lb_test', 'keri', 'cf', 'main', 'test.json')
        assert cfr.path.startswith(os.path.join(tempDirPath, 'keri_'))
        assert cfr.path.endswith(os.path.join('_test', 'keri', 'cf', 'main', 'test.json'))
        assert cfr.opened
        assert cfr.human
        assert os.path.exists(cfr.path)
        assert cfr.file
        assert not cfr.file.closed
        wdata = dict(name="hope", oobi="abc")
        assert cfr.put(wdata)
        rdata = cfr.get()
        assert rdata == wdata
    assert not os.path.exists(cfr.path)  # if temp cleans

    #test openCF json
    with configing.openCF(human=False) as cfr:  # default uses json and temp==True
        filepath = os.path.join(tempDirPath,'keri_cf_2_zu01lb_test/keri/cf/main/test.json')
        assert cfr.path.startswith(os.path.join(tempDirPath, 'keri_'))
        assert cfr.path.endswith(os.path.join('_test', 'keri', 'cf', 'main', 'test.json'))
        assert cfr.opened
        assert not cfr.human
        assert os.path.exists(cfr.path)
        assert cfr.file
        assert not cfr.file.closed
        wdata = dict(name="hope", oobi="abc")
        assert cfr.put(wdata)
        rdata = cfr.get()
        assert rdata == wdata
    assert not os.path.exists(cfr.path)  # if temp cleans

    #test openCF mgpk
    with configing.openCF(fext='mgpk') as cfr:  # default uses temp==True
        assert cfr.path.startswith(os.path.join(tempDirPath, 'keri_'))
        assert cfr.path.endswith(os.path.join('_test', 'keri', 'cf', 'main', 'test.mgpk'))
        assert cfr.opened
        assert os.path.exists(cfr.path)
        assert cfr.file
        assert not cfr.file.closed
        wdata = dict(name="hope", oobi="abc")
        assert cfr.put(wdata)
        rdata = cfr.get()
        assert rdata == wdata
    assert not os.path.exists(cfr.path)  # if temp cleans

    # test openCF cbor
    with configing.openCF(fext='cbor') as cfr:  # default uses temp==True
        assert cfr.path.startswith(os.path.join(tempDirPath, 'keri_'))
        assert cfr.path.endswith(os.path.join('_test', 'keri', 'cf', 'main', 'test.cbor'))
        assert cfr.opened
        assert os.path.exists(cfr.path)
        assert cfr.file
        assert not cfr.file.closed
        wdata = dict(name="hope", oobi="abc")
        assert cfr.put(wdata)
        rdata = cfr.get()
        assert rdata == wdata
    assert not os.path.exists(cfr.path)  # if temp cleans

    """Done Test"""


def test_configer_doer():
    """
    Test ConfigerDoer
    """
    cfr0 = configing.Configer(name='test0', temp=True, reopen=False)
    assert cfr0.opened == False
    assert cfr0.path == None
    assert cfr0.file == None

    cfrDoer0 = configing.ConfigerDoer(configer=cfr0)
    assert cfrDoer0.configer == cfr0
    assert cfrDoer0.configer.opened == False

    cfr1 = configing.Configer(name='test1', temp=True, reopen=False)
    assert cfr1.opened == False
    assert cfr1.path == None
    assert cfr0.file == None

    cfrDoer1 = configing.ConfigerDoer(configer=cfr1)
    assert cfrDoer1.configer == cfr1
    assert cfrDoer1.configer.opened == False

    limit = 0.25
    tock = 0.03125
    doist = doing.Doist(limit=limit, tock=tock)

    doers = [cfrDoer0, cfrDoer1]

    doist.doers = doers
    doist.enter()
    assert len(doist.deeds) == 2
    assert [val[1] for val in doist.deeds] == [0.0, 0.0]  #  retymes
    for doer in doers:
        assert doer.configer.opened
        assert os.path.join('_test', 'keri', 'cf', 'main') in doer.configer.path

    doist.recur()
    assert doist.tyme == 0.03125  # on next cycle
    assert len(doist.deeds) == 2
    for doer in doers:
        assert doer.configer.opened == True

    for dog, retyme, index in doist.deeds:
        dog.close()

    for doer in doers:
        assert doer.configer.opened == False
        assert not os.path.exists(doer.configer.path)

    # start over
    doist.tyme = 0.0
    doist.do(doers=doers)
    assert doist.tyme == limit
    for doer in doers:
        assert doer.configer.opened == False
        assert not os.path.exists(doer.configer.path)

    # test with filed == True
    cfr0 = configing.Configer(name='test0', temp=True, reopen=False, filed=True)
    assert cfr0.opened == False
    assert cfr0.path == None
    assert cfr0.file == None

    cfrDoer0 = configing.ConfigerDoer(configer=cfr0)
    assert cfrDoer0.configer == cfr0
    assert cfrDoer0.configer.opened == False

    cfr1 = configing.Configer(name='test1', temp=True, reopen=False, filed=True)
    assert cfr1.opened == False
    assert cfr1.path == None
    assert cfr0.file == None

    cfrDoer1 = configing.ConfigerDoer(configer=cfr1)
    assert cfrDoer1.configer == cfr1
    assert cfrDoer1.configer.opened == False

    limit = 0.25
    tock = 0.03125
    doist = doing.Doist(limit=limit, tock=tock)

    doers = [cfrDoer0, cfrDoer1]

    doist.doers = doers
    doist.enter()
    assert len(doist.deeds) == 2
    assert [val[1] for val in doist.deeds] == [0.0, 0.0]  #  retymes
    for doer in doers:
        assert doer.configer.opened
        assert os.path.join('_test', 'keri', 'cf', 'main') in doer.configer.path
        assert  doer.configer.path.endswith(".json")
        assert doer.configer.file is not None
        assert not doer.configer.file.closed

    doist.recur()
    assert doist.tyme == 0.03125  # on next cycle
    assert len(doist.deeds) == 2
    for doer in doers:
        assert doer.configer.opened
        assert doer.configer.file is not None
        assert not doer.configer.file.closed

    for dog, retyme, index in doist.deeds:
        dog.close()

    for doer in doers:
        assert doer.configer.opened == False
        assert not os.path.exists(doer.configer.path)
        assert doer.configer.file is None

    # start over
    doist.tyme = 0.0
    doist.do(doers=doers)
    assert doist.tyme == limit
    for doer in doers:
        assert doer.configer.opened == False
        assert not os.path.exists(doer.configer.path)
        assert doer.configer.file is None

    """End Test"""




def test_sourcer_abc():
    """Test Sourcer ABC and isinstance relationships."""
    from keri.app.configing import Sourcer, FileConfiger, DictConfiger

    # Cannot instantiate Sourcer directly
    with pytest.raises(TypeError):
        Sourcer()

    # FileConfiger (file-based) is a Sourcer
    cf = configing.FileConfiger(name="abc_test", temp=True, reopen=True)
    assert isinstance(cf, Sourcer)
    cf.close(clear=True)

    # Configer alias resolves to FileConfiger
    assert configing.Configer is FileConfiger

    # DictConfiger (in-memory) is a Sourcer
    dcf = DictConfiger(name="abc_test", temp=True)
    assert isinstance(dcf, Sourcer)

    # Both have the required interface
    for obj in (dcf,):
        assert hasattr(obj, 'get')
        assert hasattr(obj, 'put')
        assert hasattr(obj, 'opened')
        assert hasattr(obj, 'name')
        assert hasattr(obj, 'base')
        assert hasattr(obj, 'temp')


def test_dict_configer():
    """Test DictConfiger in-memory implementation."""
    from keri.app.configing import DictConfiger

    # defaults
    cf = DictConfiger()
    assert cf.name == "conf"
    assert cf.base == "main"
    assert cf.temp is False
    assert cf.opened is True
    assert cf.path is None

    # initially empty
    assert cf.get() == {}

    # put / get
    data = {"dt": "2021-01-01T00:00:00.000000+00:00",
            "iurls": ["tcp://localhost:5621/"]}
    assert cf.put(data) is True
    assert cf.get() == data

    # get returns a copy
    conf = cf.get()
    conf["extra"] = True
    assert "extra" not in cf.get()

    # put stores a copy
    data2 = {"new": "data"}
    cf.put(data2)
    data2["mutated"] = True
    assert "mutated" not in cf.get()

    # close / reopen
    cf.close()
    assert cf.opened is False
    assert cf.reopen() is True
    assert cf.opened is True
    assert cf.get() == {"new": "data"}  # data persists in memory

    # initial data
    cf2 = DictConfiger(data={"key": "value"}, name="test", base="base", temp=True)
    assert cf2.name == "test"
    assert cf2.base == "base"
    assert cf2.temp is True
    assert cf2.get() == {"key": "value"}


def test_dict_configer_with_habery():
    """Test Habery accepts injected DictConfiger."""
    from keri import core
    from keri.app import habbing
    from keri.app.configing import DictConfiger

    salt = core.Salter(raw=b'0123456789abcdef').qb64
    cf = DictConfiger(name="test", temp=True)

    with habbing.openHby(name="test", temp=True, salt=salt, cf=cf) as hby:
        assert hby.cf is cf
        assert hby.cf.opened is True
        assert hby.inited is True

        hab = hby.makeHab("test")
        assert hab is not None
        assert hab.pre in hby.habs


def test_endage():
    """Test Endage dataclass construction and defaults."""
    from keri.app.configing import Endage

    # defaults
    e = Endage()
    assert e.dt == ''
    assert e.curls == []

    # with values
    e = Endage(dt="2021-01-01T00:00:00.000000+00:00",
               curls=["tcp://localhost:5621/"])
    assert e.dt == "2021-01-01T00:00:00.000000+00:00"
    assert e.curls == ["tcp://localhost:5621/"]


def test_bootage():
    """Test Bootage dataclass construction and defaults."""
    from keri.app.configing import Bootage

    # defaults
    b = Bootage()
    assert b.dt == ''
    assert b.iurls == []
    assert b.durls == []
    assert b.wurls == []

    # with values
    b = Bootage(dt="2021-01-01T00:00:00.000000+00:00",
                iurls=["tcp://localhost:5620/"],
                durls=["http://127.0.0.1:7723/oobi/ABC"],
                wurls=["http://127.0.0.1:5644/.well-known/keri/oobi/ABC"])
    assert b.dt == "2021-01-01T00:00:00.000000+00:00"
    assert len(b.iurls) == 1
    assert len(b.durls) == 1
    assert len(b.wurls) == 1


def test_confitage():
    """Test Confitage dataclass construction and defaults."""
    from keri.app.configing import Confitage, Bootage, Endage

    # defaults
    c = Confitage()
    assert isinstance(c.boot, Bootage)
    assert c.boot.dt == ''
    assert c.habs == {}
    assert c.extra == {}

    # with values
    c = Confitage(
        boot=Bootage(dt="2021-01-01T00:00:00.000000+00:00"),
        habs={"nel": Endage(dt="2021-01-01T00:00:00.000000+00:00",
                            curls=["tcp://localhost:5621/"])},
        extra={"keria": {"curls": ["http://localhost:3902/"]}}
    )
    assert c.boot.dt == "2021-01-01T00:00:00.000000+00:00"
    assert "nel" in c.habs
    assert c.habs["nel"].curls == ["tcp://localhost:5621/"]
    assert "keria" in c.extra


def test_parseconfig_empty():
    """Test parseConfig with empty input."""
    from keri.app.configing import parseConfig, Confitage, Bootage

    c = parseConfig({})
    assert isinstance(c, Confitage)
    assert isinstance(c.boot, Bootage)
    assert c.boot.dt == ''
    assert c.boot.iurls == []
    assert c.habs == {}
    assert c.extra == {}

    c = parseConfig(None)
    assert isinstance(c, Confitage)
    assert c.habs == {}


def test_parseconfig_global_only():
    """Test parseConfig with global-only config (no hab sections)."""
    from keri.app.configing import parseConfig

    raw = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": ["tcp://localhost:5620/?role=peer&name=tam"],
        "durls": ["http://127.0.0.1:7723/oobi/ABC"],
        "wurls": ["http://127.0.0.1:5644/.well-known/keri/oobi/ABC"],
    }
    c = parseConfig(raw)
    assert c.boot.dt == "2021-01-01T00:00:00.000000+00:00"
    assert c.boot.iurls == ["tcp://localhost:5620/?role=peer&name=tam"]
    assert c.boot.durls == ["http://127.0.0.1:7723/oobi/ABC"]
    assert c.boot.wurls == ["http://127.0.0.1:5644/.well-known/keri/oobi/ABC"]
    assert c.habs == {}
    assert c.extra == {}


def test_parseconfig_legacy_flat():
    """Test parseConfig with legacy flat format (hab sections at top level)."""
    from keri.app.configing import parseConfig, Endage

    raw = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "nel": {
            "dt": "2021-01-01T00:00:00.000000+00:00",
            "curls": ["tcp://localhost:5621/"],
        },
        "iurls": ["tcp://localhost:5620/?role=peer&name=tam"],
        "durls": [],
        "wurls": [],
    }
    c = parseConfig(raw)
    assert c.boot.dt == "2021-01-01T00:00:00.000000+00:00"
    assert c.boot.iurls == ["tcp://localhost:5620/?role=peer&name=tam"]
    assert "nel" in c.habs
    assert isinstance(c.habs["nel"], Endage)
    assert c.habs["nel"].dt == "2021-01-01T00:00:00.000000+00:00"
    assert c.habs["nel"].curls == ["tcp://localhost:5621/"]


def test_parseconfig_new_habs_format():
    """Test parseConfig with new explicit 'habs' key format."""
    from keri.app.configing import parseConfig, Endage

    raw = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": ["tcp://localhost:5620/"],
        "habs": {
            "nel": {
                "dt": "2021-01-01T00:00:00.000000+00:00",
                "curls": ["tcp://localhost:5621/"],
            },
            "wan": {
                "dt": "2021-01-01T00:00:00.000000+00:00",
                "curls": ["tcp://localhost:5622/"],
            },
        },
    }
    c = parseConfig(raw)
    assert c.boot.dt == "2021-01-01T00:00:00.000000+00:00"
    assert c.boot.iurls == ["tcp://localhost:5620/"]
    assert len(c.habs) == 2
    assert "nel" in c.habs and "wan" in c.habs
    assert isinstance(c.habs["nel"], Endage)
    assert c.habs["nel"].curls == ["tcp://localhost:5621/"]
    assert c.habs["wan"].curls == ["tcp://localhost:5622/"]


def test_parseconfig_extra_keys():
    """Test parseConfig with extra keys (keria pattern)."""
    from keri.app.configing import parseConfig

    # Legacy format: non-hab dict without 'curls' goes to extra
    raw = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": [],
        "keria": {
            "curls": ["http://localhost:3902/"],
        },
        "other_key": "some_value",
    }
    c = parseConfig(raw)
    # keria has curls so is treated as hab in legacy mode
    assert "keria" in c.habs
    assert "other_key" in c.extra

    # New format: extra keys preserved
    raw2 = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "habs": {},
        "keria": {"curls": ["http://localhost:3902/"]},
    }
    c2 = parseConfig(raw2)
    assert "keria" in c2.extra
    assert c2.habs == {}


def test_parseconfig_backward_compat():
    """Test that both formats produce equivalent Confitage for same data."""
    from keri.app.configing import parseConfig

    # Legacy format
    legacy = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": ["tcp://localhost:5620/"],
        "durls": [],
        "wurls": [],
        "nel": {
            "dt": "2021-01-01T00:00:00.000000+00:00",
            "curls": ["tcp://localhost:5621/"],
        },
    }
    # New format
    new = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": ["tcp://localhost:5620/"],
        "durls": [],
        "wurls": [],
        "habs": {
            "nel": {
                "dt": "2021-01-01T00:00:00.000000+00:00",
                "curls": ["tcp://localhost:5621/"],
            },
        },
    }
    cl = parseConfig(legacy)
    cn = parseConfig(new)
    assert cl.boot == cn.boot
    assert cl.habs == cn.habs


def test_parseconfig_integration_dictconfiger():
    """Integration: DictConfiger -> parseConfig -> Confitage."""
    from keri.app.configing import DictConfiger, parseConfig, Endage

    raw = {
        "dt": "2021-01-01T00:00:00.000000+00:00",
        "iurls": ["tcp://localhost:5620/"],
        "nel": {
            "dt": "2021-01-01T00:00:00.000000+00:00",
            "curls": ["tcp://localhost:5621/"],
        },
    }
    cf = DictConfiger(data=raw)
    c = parseConfig(cf.get())
    assert c.boot.dt == "2021-01-01T00:00:00.000000+00:00"
    assert c.boot.iurls == ["tcp://localhost:5620/"]
    assert "nel" in c.habs
    assert isinstance(c.habs["nel"], Endage)
    assert c.habs["nel"].curls == ["tcp://localhost:5621/"]


if __name__ == "__main__":
    test_configer()
