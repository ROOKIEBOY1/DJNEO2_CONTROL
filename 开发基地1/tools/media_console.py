"""Neo2 capture-backed media list/extraction/download. Offline by default."""
import argparse
import json
import http.client
from pathlib import Path

from media_transfer import HOST,download_entry,extract_capture

DEFAULT_MANIFEST=(Path(__file__).resolve().parent.parent/'captures'/
                  'media-evidence-20261030-141959-final'/'manifest.json')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    extract=commands.add_parser('extract',help='离线重组抓包并提取完整原片；输出目录须不存在')
    extract.add_argument('capture',type=Path)
    extract.add_argument('--output',type=Path,required=True)
    listing=commands.add_parser('list',help='仅列出本次捕获清单，不查询设备最新相册')
    listing.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    download=commands.add_parser('download',help='默认仅显示计划；--connect 才重放原样媒体 GET')
    download.add_argument('name',help='捕获清单中的原片文件名')
    download.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    download.add_argument('--output',type=Path,required=True,help='新的完整文件路径，拒绝覆盖')
    download.add_argument('--connect',action='store_true',help='连接飞机 Wi-Fi 后，允许只读媒体 GET')
    args=parser.parse_args(argv)
    try:
        if args.command=='extract':
            manifest=extract_capture(args.capture,args.output)
            print('证据及完整原片已保存：',args.output.resolve())
            print('完整原片数：',sum(item['complete'] for item in manifest['entries']))
            return 0
        manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
        if manifest.get('schema')!=1 or manifest.get('host')!=HOST:
            raise ValueError('不支持的捕获清单或主机')
        print('仅本次捕获清单；不是设备最新相册。未观察到目录列表接口。')
        if args.command=='list':
            for item in manifest['entries']:
                state='已完整提取' if item['complete'] else ('可原样重下，抓包有缺口' if item['replayable'] else '仅预览片段，不能下载原片')
                print(f"{item['name']}  {item['size']:,} 字节  {state}")
            return 0
        matches=[entry for entry in manifest['entries'] if entry['name']==args.name and entry['replayable']]
        if len(matches)!=1:
            raise ValueError('必须选择清单中唯一、已观察全部下载 GET 的原片')
        entry=matches[0]
        print(f"GET http://{HOST}  {entry['name']}  {entry['size']:,} 字节；{len(entry['requests'])} 段")
        if not args.connect:
            print('离线计划；未发送请求。连接飞机 Wi-Fi 后加 --connect 执行。')
            return 0
        result=download_entry(entry,args.output,connect=True)
        print('已校验并保存：',args.output.resolve())
        print('SHA256:',result['sha256'])
        return 0
    except (OSError,ValueError,KeyError,TypeError,http.client.HTTPException) as exc:
        parser.exit(2,f'媒体操作失败：{exc}\n')


if __name__=='__main__':
    raise SystemExit(main())
