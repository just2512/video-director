"""确认环节端到端验证：暂停→分镜书→动态草稿→finalize→final。"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from video_director import confirm, pipeline, prober, store

script = Path(r'D:\autocut\测试视频与文案\视频文案.md').read_text(encoding='utf-8')
vids = [str(p) for p in sorted(Path(r'D:\autocut\测试视频与文案').glob('*.mp4'))]
pid = 'confirm-e2e-4'

# 1. auto_confirm=False → 暂停
r = pipeline.compose_auto(script=script, video_files=vids, image_files=[],
                          project_id=pid, auto_confirm=False,
                          manual_manifest=r'D:\autocut\测试视频与文案\assets_semantic.json',
                          width=720, height=1280, fps=25)
print('1. pending_confirm:', r.pending_confirm)
print('   分镜书场景数:', len(r.storyboard['scenes']))
print('   final 未渲染:', r.final_path == '')
rec = store.get_project(pid)
print('   stage:', rec['stage'])

# 2. Gate B 动态草稿
draft = confirm.render_draft(pid)
p = prober.probe_media(draft)
dname = Path(draft).name
print('2. draft:', dname, f'{p.width}x{p.height}', f'{p.duration:.2f}s')
assert p.width == 360 and p.height == 640, '草稿应为半分辨率'

# 3. finalize → final
final = confirm.finalize_after_review(pid)
p2 = prober.probe_media(final.final_path)
fname = Path(final.final_path).name
print('3. final:', fname, f'{p2.width}x{p2.height}', f'{p2.duration:.2f}s')
print('   QC:', final.qc_report['verdict'])
assert final.qc_report['verdict'] == 'PASS', 'final QC 必须 PASS'
rec2 = store.get_project(pid)
print('   stage:', rec2['stage'], '| draft 仍在:', Path(draft).is_file())
print('E2E CONFIRM PASS')