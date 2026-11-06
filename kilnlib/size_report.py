"""Read-only PSP install size audit: python -m kilnlib.size_report OUTPUT_OR_DATA."""
from pathlib import Path
from collections import defaultdict
import argparse,json

def report(root):
    root=Path(root)
    data=root/"data" if (root/"data").is_dir() else root
    groups=defaultdict(int)
    files=[]
    for path in data.rglob("*"):
        if not path.is_file(): continue
        rel=path.relative_to(data).as_posix()
        size=path.stat().st_size
        group=rel.split("/")[0] if "/" in rel else path.stem.split("_")[0]
        groups[group]+=size
        files.append((size,rel))
    return {"data":str(data.resolve()),"bytes":sum(groups.values()),
            "groups":dict(sorted(groups.items(),key=lambda x:-x[1])),
            "largest":[{"path":n,"bytes":b} for b,n in sorted(files,reverse=True)[:20]]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    if not args.folder.is_dir(): parser.error("folder does not exist")
    result=report(args.folder)
    if args.json: print(json.dumps(result,indent=2))
    else:
        print(f"{result['data']}: {result['bytes']/1024**3:.2f} GiB")
        for name,size in result["groups"].items(): print(f"{size/1024**3:8.3f} GiB  {name}")
        print("Smaller rebuild: --compact. Extra locales, work/, logs/ and backup copies need not be on the memory stick.")
        print("Use a separate output folder to compare sizes; no files are removed by this report.")
if __name__=="__main__": main()
