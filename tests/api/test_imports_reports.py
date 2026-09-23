from io import BytesIO
from time import monotonic, sleep
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from moneygraph.api import create_app
from moneygraph.api.demo import demo_snapshot
from moneygraph.api.excel import workbook_bytes
from moneygraph.api.service import QueryService

HEADERS = {'X-Moneygraph-Client': 'local-ui', 'Origin': 'http://127.0.0.1'}
SPEC = {'period_start': '2026-07-01', 'period_end': '2026-07-31'}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def files(day='2026-07-10'):
    gid = 100000006866783123
    frames = {
        'nodes.parquet': pd.DataFrame({'gid': [gid,gid+1,gid+2], 'depth':[0,1,0], 'is_seed':[True,False,True]}),
        'edges.parquet': pd.DataFrame({'src':[gid], 'dst':[gid+1], 'sum_kzt':[10000.01], 'n_tx':[2], 'depth':[1]}),
        'transactions.parquet': pd.DataFrame({'src':[gid,gid], 'dst':[gid+1,gid+1], 'date':[day,day], 'sum_kzt':[5000.,5000.01]}),
    }
    return {name: frame.to_parquet(index=False) for name,frame in frames.items()}


def finish(client, job):
    response = client.post(f'/api/v1/imports/{job}/run', headers=HEADERS)
    assert response.status_code == 202, response.text
    deadline = monotonic()+20
    while monotonic()<deadline:
        result=client.get('/api/v1/imports/'+job).json()
        if result['state'] != 'running': return result
        sleep(.025)
    pytest.fail('Local import did not finish')


def upload(client, payload):
    response=client.post('/api/v1/imports',json=SPEC,headers=HEADERS)
    assert response.status_code==201, response.text
    job=response.json()['id']
    for name,content in payload.items():
        response=client.put(f'/api/v1/imports/{job}/files/{name}',content=content,headers=HEADERS)
        assert response.status_code==200,response.text
    return job


def test_upload_real_pipeline_atomic_switch_and_previous_run(tmp_path):
    with TestClient(create_app(demo_snapshot(),import_dir=tmp_path),base_url='http://127.0.0.1') as client:
        old=client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content
        job=upload(client,files())
        assert client.get('/health').json()['run_id']=='demo-v1'
        result=finish(client,job)
        assert result['state']=='complete',result
        run=result['run_id']
        assert client.get('/health').json()['run_id']==run
        assert client.get('/api/v1/runs/demo-v1/exports/nodes_roles.csv').content==old
        summary=client.get('/api/v1/runs/current').json()['data']
        assert summary['stats']['n_nodes']==3 and summary['stats']['sum_minor']=='1000001'
        groups=client.get(f'/api/v1/runs/{run}/clusters').json()['data']['items']
        isolated=next(g for g in groups if g['n_operations']==0)
        assert 'не нулевой баланс' in isolated['zero_explanation']
        report=client.get(f'/api/v1/runs/{run}/reports/moneygraph.xlsx')
        assert report.status_code==200
        with ZipFile(BytesIO(report.content)) as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet2.xml'))
            gids=sheet.findall('.//s:c[@t="inlineStr"]/s:is/s:t',NS)
            assert any(c.text=='100000006866783123' for c in gids)
        bad=upload(client,files('2026-08-01'))
        failed=finish(client,bad)
        assert failed['state']=='failed' and 'периода' in failed['message']
        assert client.get('/health').json()['run_id']==run
        assert not list(tmp_path.glob('.upload-*'))


def test_upload_validation_origin_limits_and_concurrency(tmp_path,monkeypatch):
    with TestClient(create_app(import_dir=tmp_path),base_url='http://127.0.0.1') as client:
        assert client.post('/api/v1/imports',json=SPEC).status_code==403
        assert client.post('/api/v1/imports',json=SPEC,headers={**HEADERS,'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/v1/imports',json=SPEC,headers={**HEADERS,'Host':'evil.example'}).status_code==403
        assert client.post('/api/v1/imports',json={**SPEC,'period_end':'2026-06-01'},headers=HEADERS).status_code==422
        job=client.post('/api/v1/imports',json=SPEC,headers=HEADERS).json()['id']
        assert client.post('/api/v1/imports',json=SPEC,headers=HEADERS).status_code==409
        assert client.post(f'/api/v1/imports/{job}/run',headers=HEADERS).status_code==422
        assert client.put(f'/api/v1/imports/{job}/files/secret.txt',content=b'x',headers=HEADERS).status_code==422
        assert client.put(f'/api/v1/imports/{job}/files/nodes.parquet',content=b'not parquet',headers=HEADERS).status_code==422
        job=client.post('/api/v1/imports',json=SPEC,headers=HEADERS).json()['id']
        monkeypatch.setattr('moneygraph.api.imports.MAX_BYTES',3)
        assert client.put(f'/api/v1/imports/{job}/files/nodes.parquet',content=b'1234',headers=HEADERS).status_code==413


def test_xlsx_unicode_exact_ids_and_unmodified_csv():
    payload=demo_snapshot()
    payload['nodes'][0]['evidence']='=HYPERLINK("https://example.com")'
    service=QueryService(payload)
    before=dict(service.exports)
    result=workbook_bytes(service)
    with ZipFile(BytesIO(result)) as archive:
        assert 'Как читать' in archive.read('xl/workbook.xml').decode('utf-8')
        for name in archive.namelist():
            if name.endswith('.xml'):
                root=ET.fromstring(archive.read(name))
                assert not root.findall('.//s:f',NS)
        assert 'Номер клиента' in archive.read('xl/worksheets/sheet2.xml').decode('utf-8')
    assert service.exports==before
