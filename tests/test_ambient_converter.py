import unittest,tempfile,os,struct,wave,subprocess
from pathlib import Path
@unittest.skipUnless(os.environ.get("KILN_SOUND_CONVERTER"),"set KILN_SOUND_CONVERTER for integration")
class AmbientConverterTest(unittest.TestCase):
    def test_authored_areas_weather_and_bounded_pcm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/"dbfilesclient";db.mkdir()
            def dbc(name,rows,strings=b"\0"):
                width=len(rows[0]);data=b"".join(struct.pack("<"+"I"*width,*row) for row in rows)
                (db/(name+".dbc")).write_bytes(struct.pack("<4s4I",b"WDBC",len(rows),width,width*4,len(strings))+data+strings)
            dbc("areatable",[[100,0,0,0,0,0,0,7],[101,0,100,0,0,0,0,0]])
            dbc("soundambience",[[7,500,500]])
            pool=bytearray(b"\0")
            def text(value):
                offset=len(pool);pool.extend(value.encode()+b"\0");return offset
            rows=[]
            for ident,folder,file in [(500,"Sound\\Ambience\\Zone","zone.wav"),(501,"Sound\\Ambience\\Weather","RainHeavyLoop.wav")]:
                row=[0]*28;row[0]=ident;row[1]=50;row[2]=text(file);row[3]=text(file);row[23]=text(folder)
                row[24]=struct.unpack("<I",struct.pack("<f",.5))[0];rows.append(row)
                path=root/folder.replace("\\","/").lower()/file.lower();path.parent.mkdir(parents=True,exist_ok=True)
                with wave.open(str(path),"wb") as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(22050);wav.writeframes(b"\0\0"*(12*22050))
            dbc("soundentries",rows,bytes(pool))
            out=root/"sounds.pak"
            result=subprocess.run([os.environ["KILN_SOUND_CONVERTER"],"--extracted",str(root),"--out",str(out),"--trim-long","--verify"],capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            data=out.read_bytes();magic,version,count,toc=struct.unpack_from("<4s3I",data)
            entries={}
            for i in range(count):
                name,offset,size,kind,reserved=struct.unpack_from("<48s4I",data,toc+i*64);entries[name.split(b"\0")[0].decode()]=data[offset:offset+size]
            self.assertEqual((100,0,500,500),struct.unpack_from("<4I",entries["ambience"]))
            clips=[blob for blob in entries.values() if blob[:4]==b"WSND"]
            self.assertEqual(2,len(clips))
            for clip in clips:
                _,_,channels,_,rate,frames=struct.unpack_from("<4sHBBII",clip)
                self.assertEqual(1,channels);self.assertEqual(11025,rate);self.assertLessEqual(frames,6*11025)
