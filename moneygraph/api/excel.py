"""Small read-only OOXML report writer. IDs are text cells, never Excel numbers."""
from decimal import Decimal
from io import BytesIO
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from .presentation import ROLE_LABELS, explain_node

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def _text(value):
    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value)))


def _money(value):
    amount = Decimal(value) / 100
    # Excel stores only 15 significant digits. Keep larger amounts exact as text.
    return amount if len(str(abs(int(value)))) <= 15 else str(amount)


def workbook_bytes(service):
    """Separate presentation report; the three required CSV artifacts stay intact."""
    nodes = [[n.gid, ROLE_LABELS[n.role], explain_node(n), round(n.priority_score * 100, 2),
              round(n.role_score * 100, 2), n.cluster_id,
              _money(n.metrics['in_minor']) if 'in_minor' in n.metrics else 'Нет данных',
              _money(n.metrics['out_minor']) if 'out_minor' in n.metrics else 'Нет данных'] for n in service.ranked]
    groups = [[c['cluster_id'], c['n_nodes'], c['n_seed'], _money(c['sum_minor_internal']),
               _money(c['incoming_minor']), _money(c['outgoing_minor']),
               c['description'] + ' ' + c['zero_explanation'], ', '.join(c['top_gids'])] for c in service.clusters]
    top = [[r['rank'], str(r['gid']), ROLE_LABELS[r['role']], round(float(r['priority_score']) * 100, 2),
            explain_node(service.nodes[str(r['gid'])])] for r in service.top_rows]
    sheets = [
        ('Как читать', ['Показатель', 'Объяснение'], [
            ['Расчёт', service.run_id], ['Источник', 'Демонстрационные данные' if service.manifest.get('demo') else 'Загруженные данные'],
            ['Приоритет, 0–100', 'Кого полезнее проверить раньше. Это не вероятность нарушения.'],
            ['Оценка роли, 0–100', 'Насколько ясно выражена предполагаемая роль с учётом ограничений.'],
            ['Переводы внутри группы', 'Сумма переводов между её участниками. Не баланс счетов.'],
            ['Исходные клиенты', 'Клиенты, от которых начался поиск цепочек переводов.'],
            ['Номера клиентов', 'Сохранены как текст: все цифры должны остаться неизменными.'],
            ['Ограничения', 'Сеть неполная, четвёртое колено обрывается. Роли — предположения для проверки.'],
            ['CSV по ТЗ', 'Скачиваются отдельно без изменения исходных файлов; для Excel используйте этот XLSX.'],
        ], [29, 100]),
        ('Клиенты', ['Номер клиента', 'Предполагаемая роль', 'Что видно по переводам', 'Приоритет, 0–100', 'Оценка роли, 0–100', 'Группа', 'Получено, ₸', 'Отправлено, ₸'], nodes, [24, 29, 90, 20, 22, 12, 22, 22]),
        ('Группы', ['Группа', 'Клиентов', 'Исходных клиентов', 'Внутри группы, ₸', 'Из других групп, ₸', 'В другие группы, ₸', 'Понятное объяснение', 'Ключевые клиенты'], groups, [12, 14, 23, 23, 23, 23, 95, 65]),
        ('Проверить сначала', ['Место', 'Номер клиента', 'Предполагаемая роль', 'Приоритет, 0–100', 'Что видно по переводам'], top, [12, 24, 30, 23, 95]),
    ]
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as out:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1))
        out.writestr('[Content_Types].xml', f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{overrides}</Types>')
        out.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        out.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{_text(s[0])}" sheetId="{i}" r:id="rId{i}"/>' for i,s in enumerate(sheets,1)) + '</sheets></workbook>')
        out.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + '<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        out.writestr('xl/styles.xml', f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF174B3D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for idx, (_, headers, rows, widths) in enumerate(sheets,1):
            body = []
            for ridx, row in enumerate([headers, *rows],1):
                cells=[]
                for cidx, value in enumerate(row):
                    address=f'{chr(65+cidx)}{ridx}'
                    numeric = isinstance(value,(int,float,Decimal)) and not isinstance(value,bool)
                    style=1 if ridx==1 else 2 if isinstance(value,Decimal) else 0
                    cells.append(f'<c r="{address}" s="{style}"><v>{value}</v></c>' if numeric else f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>')
                body.append(f'<row r="{ridx}" ht="{32 if ridx==1 else 58}" customHeight="1">' + ''.join(cells) + '</row>')
            cols=''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths,1))
            out.writestr(f'xl/worksheets/sheet{idx}.xml',f'<worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>{cols}</cols><sheetData>{"".join(body)}</sheetData><autoFilter ref="A1:{chr(64+len(headers))}{len(rows)+1}"/></worksheet>')
    return stream.getvalue()
