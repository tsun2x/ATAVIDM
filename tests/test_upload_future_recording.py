import io


def test_upload_rejects_future_recording_before_saving(enforcer_client, test_db):
    before = len(test_db.list_videos())
    response = enforcer_client.post('/api/upload-video', data={
        'recorded_at': '2099-01-01 12:00:00', 'condition': 'peak',
        'video': (io.BytesIO(b'video'), 'future.mp4'),
    }, content_type='multipart/form-data')
    assert response.status_code == 400
    assert 'future' in response.get_json()['error'].lower()
    assert len(test_db.list_videos()) == before
