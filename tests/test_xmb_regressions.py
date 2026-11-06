import unittest,tempfile,struct,hashlib
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from kilnlib import xmb,check
from test_updates import pbp
class XmbRegressionTests(unittest.TestCase):
    def test_art_embedded_without_modifying_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/"original.pbp";dst=root/"EBOOT.PBP"
            sec={name:b"" for name in xmb.PBP_SECTIONS};sec["DATA.PSP"]=b"executable";sec["PARAM.SFO"]=b"settings"
            src.write_bytes(xmb.write_pbp(0x10000,sec))
            for name,size in [("ICON0.PNG",(144,80)),("PIC1.PNG",(480,272))]: Image.new("RGBA",size,(20,40,60,255)).save(root/name)
            xmb.repack_eboot(str(src),str(dst),str(root/"ICON0.PNG"),str(root/"PIC1.PNG"))
            _,new=xmb.read_pbp(dst.read_bytes())
            self.assertEqual(new["DATA.PSP"],sec["DATA.PSP"])
            self.assertEqual(new["PARAM.SFO"],sec["PARAM.SFO"])
            self.assertGreater(len(new["ICON0.PNG"]),0)
            self.assertGreater(len(new["PIC1.PNG"]),0)
            self.assertEqual(xmb.executable_identity(src),xmb.executable_identity(dst))
            self.assertEqual(str(dst),xmb.resolve_eboot(target=str(root)))
    def test_malformed_offsets_rejected(self):
        for data in (b"\0PBP", b"\0PBP"+struct.pack("<I8I",0x10000,40,39,40,40,40,40,40,40)):
            with self.assertRaises(ValueError): xmb.read_pbp(data)
    def test_cli_copies_repacked_eboot_to_game_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/"out";target=root/"stick";(out/"data").mkdir(parents=True);(out/"xmb").mkdir()
            art=pbp(code=b"repacked executable");(out/"xmb/EBOOT.PBP").write_bytes(art)
            man=SimpleNamespace(doc={"steps":{"xmb_art":{"status":"ok","outputs":[{"path":"xmb/EBOOT.PBP","size":len(art),"sha256":hashlib.sha256(art).hexdigest()}]}}})
            ctx=SimpleNamespace(out=str(out),data=str(out/"data"),json=True,event=lambda *a,**k:None,say=lambda *a,**k:None)
            result=check.sync_target(ctx,man,str(target),check.Report(ctx))
            self.assertEqual(1,result[0]);self.assertEqual(art,(target/"EBOOT.PBP").read_bytes())
