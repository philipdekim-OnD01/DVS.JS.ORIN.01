import hashlib
import json
import threading
import numpy as np
import pytest
cv2 = pytest.importorskip('cv2', reason='Install the [video] extra for video tests')
from person_yolo.postprocess import annotate, frames, inputs, process


@pytest.fixture
def video(tmp_path):
    p = tmp_path/'input.avi'
    writer = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*'MJPG'), 10, (128, 96))
    assert writer.isOpened()
    for i in range(4):
        rgb = np.zeros((96, 128, 3), np.uint8)
        rgb[25:60, 10+i*5:30+i*5] = [255, 0, 0]
        writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    writer.release()
    return p


def test_video_color_and_timestamp_contract(video):
    paths, kind = inputs([video], 4)
    rows = list(frames(paths, kind, 4, [{'sha256':'test-only'}]))
    assert len(rows) == 4
    assert rows[0][0][40, 20, 0] > 240 and rows[0][0][40, 20, 2] < 15
    assert rows[-1][2]['timestamp_seconds'] == .3
    assert rows[-1][2]['sensor_timestamp_candidate_us'] is None


def test_full_dry_run_artifacts_and_preserve_input(tmp_path, video):
    before = hashlib.sha256(video.read_bytes()).hexdigest()
    out = tmp_path/'job'
    r = process([video], out, dry_run=True)
    assert r['state']=='completed' and r['processed_frames']==4 and r['inferred_frames']==0
    assert r['inputs'][0]['sha256']==before and r['audio_preserved'] is False
    rows = [json.loads(line) for line in (out/'detections.jsonl').read_text().splitlines()]
    assert all(not row['inference_ran'] and row['status']=='dry_run' and not row['raw2_direct'] for row in rows)
    assert hashlib.sha256(video.read_bytes()).hexdigest()==before
    assert not (out/'annotated.partial.mp4').exists()
    cap=cv2.VideoCapture(str(out/'annotated.mp4'))
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT)==4
    cap.release()
    with pytest.raises(ValueError, match='already exists'): process([video], out, dry_run=True)


def test_cancellation_keeps_partial_artifacts(tmp_path, video):
    cancel = threading.Event()
    out = tmp_path/'cancel'
    r = process([video], out, dry_run=True, cancel=cancel, progress=lambda _:cancel.set())
    assert r['state']=='cancelled' and r['processed_frames']==1
    assert (out/'annotated.partial.mp4').exists() and not (out/'annotated.mp4').exists()
    assert json.loads((out/'summary.json').read_text())['state']=='cancelled'


def test_missing_model_fails_honestly(tmp_path, video):
    out=tmp_path/'missing'
    with pytest.raises(ValueError, match='weights required'): process([video], out)
    assert json.loads((out/'summary.json').read_text())['state']=='failed'
    assert not (out/'annotated.mp4').exists()


def test_bad_input_and_raw_rejected(tmp_path):
    with pytest.raises(ValueError): inputs(['http://localhost/stream'], 4)
    raw=tmp_path/'dump.zst'; raw.write_bytes(b'raw')
    with pytest.raises(ValueError, match='RAW2'): inputs([raw],4)
    bad=tmp_path/'broken.mp4'; bad.write_bytes(b'broken')
    with pytest.raises(ValueError): process([bad],tmp_path/'bad',dry_run=True)
    assert json.loads((tmp_path/'bad/summary.json').read_text())['state']=='failed'


def test_snapshot_source_provenance_and_stale_gate(tmp_path, snapshot_bytes):
    a,b=tmp_path/'a.bin',tmp_path/'b.bin'
    a.write_bytes(snapshot_bytes()); b.write_bytes(snapshot_bytes(snapshot_id=2,has_frame=False))
    rows=list(frames([a,b],'snapshot_preview_sequence',4,[]))
    assert rows[0][2]['usable'] and not rows[1][2]['usable']
    assert rows[1][2]['timestamp_seconds']==.25
    assert 'not_sensor' in rows[1][2]['timestamp_quality']


def test_annotations_do_not_change_input():
    rgb=np.zeros((96,128,3),np.uint8)
    out=annotate(rgb,[dict(score=.8,bbox_xyxy=[10,40,70,80])],'test-only')
    assert not rgb.any() and out.any()


def test_changed_input_marks_failure(tmp_path, video):
    def change(_):
        # Test input is temporary; simulate an external writer updating the file.
        import os
        stat=video.stat(); os.utime(video,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    with pytest.raises(ValueError,match='changed during'):
        process([video],tmp_path/'changed',dry_run=True,progress=change)
    assert not (tmp_path/'changed/annotated.mp4').exists()
