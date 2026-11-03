"""CMR registry, Python 3.10+. Install requests and beautifulsoup4.
Run: python cmr_registry.py. Public read-only requests, separate XLSX output.
"""
import re
import sys
import time
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal
from urllib.parse import urljoin, urlparse, parse_qs
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
import requests
from performance import parallel_map, make_session
from bs4 import BeautifulSoup

BASE = 'https://tenders.procurement.gov.ge/public/'
ENDPOINT = BASE + 'library/ssp/ssp_controller.php'
ORG = 'აფხაზეთის ავტონომიური რესპუბლიკის საინფორმაციო ანალიტიკური ცენტრი'
ROOT = Path(__file__).resolve().parent
ORG_ID = '80466'
ORG_CODE = '405384542'
DATE_FROM = '01.01.2024'
DATE_TO = '31.12.2025'
PERIOD = '2024–2025'

YEARS = {2024, 2025}


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def money(value):
    match = re.search(r'\d[\d`\s,]*(?:\.\d+)?', value)
    return Decimal(re.sub(r'[`\s,]', '', match[0])) if match else None


def dt(value):
    match = re.search(r'\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2})?', value)
    if not match:
        return None
    return datetime.strptime(match[0], '%d.%m.%Y %H:%M' if len(match[0]) > 10 else '%d.%m.%Y')


def currency(value):
    match = re.search(r'\b(GEL|USD|EUR)\b', value)
    return match[1] if match else ''


def request(session, params, post=False):
    time.sleep(0.2)
    response = session.post(ENDPOINT, data=params, timeout=90) if post else session.get(ENDPOINT, params=params, timeout=90)
    response.raise_for_status()
    response.encoding = 'utf-8'
    if not response.text.strip():
        raise ValueError(f'ცარიელი პასუხი: {params}')
    return BeautifulSoup(response.text, 'html.parser')


def read_list(soup):
    table = soup.select_one('#ssp_list')
    pager = soup.select_one('#ssp_btn_reload')
    match = re.search(r'(\d+)\s*ჩანაწერი.*?გვერდი:\s*(\d+)\s*/\s*(\d+)', text(pager))
    if table is None or not match:
        raise ValueError('CMR სიის ცხრილი ან გვერდების რაოდენობა ვერ ამოიცნო')
    rows = []
    for tr in table.select('tbody tr[id]'):
        click = re.search(r'ShowSSP\((\d+)\)', tr.get('onclick', ''))
        number = re.search(r'CMR\d+', text(tr))
        if not click or not number:
            raise ValueError('CMR ნომერი ან სისტემური ID ვერ ამოიცნო')
        rows.append((click[1], number[0], dt(text(tr.select_one('.status')))))
    return int(match[1]), int(match[2]), int(match[3]), rows


def label_data(root):
    result = {}
    if root:
        for label in root.select('.alabel'):
            value = label.find_next_sibling('div', class_='adata')
            if value:
                result[text(label)] = value
    return result


def identity(value):
    match = re.search(r'^(.*?)\s*\(([^()]+)\)', value)
    return (match[1].strip(), match[2].strip()) if match else (value, '')


def parse_detail(soup, ssp_id, number, registration):
    upper = soup.select_one('#upper')
    block = soup.select_one('#agr_block')
    if upper is None or block is None:
        raise ValueError(f'{number}: დეტალების ბლოკები ვერ მოიძებნა')
    actual_number = re.search(r'CMR\d+', text(soup))
    if actual_number is None or actual_number[0] != number:
        raise ValueError(f'{number}: პასუხში სხვა CMR ნომერია')
    top, agr = label_data(upper), label_data(block)
    supplier, supplier_code = identity(text(top.get('მიმწოდებელი')))
    buyer, buyer_code = identity(text(top.get('შემსყიდველი')))
    if buyer_code != ORG_CODE:
        raise ValueError(f'{number}: შემსყიდველის კოდი არ ემთხვევა ({buyer_code} / {ORG_CODE})')
    amounts, cpvs, funding, bases, deeds, attachments = [], [], [], [], [], []
    for sequence, tr in enumerate(top.get('თანხები', BeautifulSoup('', 'html.parser')).select('tr'), 1):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 2:
            continue
        amount = money(text(cells[1]))
        event_date = dt(text(cells[0].select_one('.date')))
        kind = text(cells[0]).split(text(cells[0].select_one('.date')))[0].strip() if cells[0].select_one('.date') else text(cells[0])
        payment = 'ფაქტობრივად გადახდილი' in kind
        amounts.append([number, sequence, 'გადახდა' if payment else 'ღირებულება / სხვა',
                        kind, event_date, amount, currency(text(cells[1])),
                        event_date.year if event_date else None,
                        'კი' if event_date and event_date.year in YEARS else 'არა' if event_date else 'უცნობია'])
    for tr in top.get('თანხები CPV-ს მიხედვით', BeautifulSoup('', 'html.parser')).select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            cpvs.append([number, text(cells[0]), money(text(cells[1]))])
    for tr in top.get('დაფინანსება', BeautifulSoup('', 'html.parser')).select('tr'):
        cell = tr.find('td')
        if not cell:
            continue
        share = re.search(r'(\d+(?:\.\d+)?)\s*%', text(cell))
        name = text(cell)
        if share:
            name = name[:share.start()].strip()
        funding.append([number, name, Decimal(share[1]) / 100 if share else None])
    for tr in top.get('შესყიდვის საფუძვლები', BeautifulSoup('', 'html.parser')).select('tr'):
        if text(tr):
            bases.append([number, text(tr)])
    for sequence, tr in enumerate(top.get('დოკუმენტი', BeautifulSoup('', 'html.parser')).select('tr'), 1):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 3:
            continue
        name = text(cells[1])
        signed = dt(text(cells[1].select_one('.date')))
        if signed:
            name = re.sub(r'\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2})?', '', name).strip()
        period = re.findall(r'\d{2}\.\d{2}\.\d{4}', text(cells[2]))
        deeds.append([number, sequence, text(cells[0]), name, signed,
                      dt(period[0]) if period else None, dt(period[1]) if len(period) > 1 else None])
    for tr in top.get('მიმაგრებული ფაილები', BeautifulSoup('', 'html.parser')).select('tr'):
        for a in tr.select('a[href]'):
            if 'library/files.php' not in a['href']:
                continue
            params = parse_qs(urlparse(a['href']).query)
            holder = a.find_parent('td')
            obsolete = re.search(r'obsolete(\d+)', ' '.join(holder.get('class', []))) if holder else None
            attachments.append([number, text(a), params.get('file', [''])[0], dt(text(tr.select_one('.date'))),
                                obsolete[1] if obsolete else '', urljoin(BASE, a['href'])])
    signed_dates = [r[4] for r in deeds if r[4]]
    relevant_contract = any(d.year in YEARS for d in signed_dates)
    relevant_payment = any(r[2] == 'გადახდა' and r[4] and r[4].year in YEARS for r in amounts)
    reason = ('ხელშეკრულების/დოკუმენტის თარიღი' if relevant_contract else '')
    if relevant_payment:
        reason = (reason + '; ' if reason else '') + 'გადახდის თარიღი'
    if not reason:
        reason = 'პერიოდთან კავშირი გადასამოწმებელია'
    value = text(agr.get('ხელშეკრულების ღირებულება'))
    curr = currency(value)
    payment_values = [r[5] for r in amounts if r[2] == 'გადახდა' and r[5] is not None and r[6] == curr]
    payment_period = [r[5] for r in amounts if r[2] == 'გადახდა' and r[5] is not None and r[6] == curr and r[8] == 'კი']
    record = [number, ssp_id, registration, text(upper.select_one('[class^="agrfg"]')),
              text(agr.get('შესყიდვის ტიპი')), supplier, supplier_code, text(agr.get('დოკუმენტის ტიპი')),
              money(value), curr, sum(payment_values, Decimal('0')), sum(payment_period, Decimal('0')),
              text(agr.get('ხელშეკრულების შესრულება')), text(agr.get('CPV კოდები (ძირითადი)')),
              text(agr.get('CPV კოდები (დეტალური)')), reason]
    record.extend([buyer_code, buyer, ORG_ID])
    return record, amounts, cpvs, funding, bases, deeds, attachments


def parse_typed(soup, number):
    pager = soup.select_one('#btn_doc_page')
    match = re.search(r'(\d+)\s*ჩანაწერი\s*/\s*გვერდი\s*(\d+)', text(pager))
    if not match:
        # Zero-result responses can omit paging controls entirely.
        visible = text(soup)
        files = soup.select('a[href*="files.php"]')
        empty_notice = re.search(
            r'0\s*ჩანაწერი|ჩანაწერ(?:ი|ები)\s*(?:არ\s*(?:არის|მოიძებნა)|ვერ\s*მოიძებნა)|'
            r'დოკუმენტ(?:ი|ები|აცია)\s*(?:არ\s*(?:არის|მოიძებნა)|მიმაგრებული\s*არ\s*არის)|'
            r'ფაილ(?:ი|ები)\s*(?:არ\s*(?:არის|მოიძებნა)|მიმაგრებული\s*არ\s*არის)', visible)
        if not files and empty_notice:
            return 0, 1, []
    if not match:
        diagnostic = ROOT / f'diagnostic_{number}_typed_docs.html'
        diagnostic.write_text(str(soup), encoding='utf-8')
        raise ValueError(
            f'{number}: თანმდევი დოკუმენტების პასუხი ვერ ამოიცნო. '
            f'პასუხი შენახულია: {diagnostic}. '
            'ეს პასუხი გამოგზავნეთ შესამოწმებლად; ცარიელ სიად ავტომატურად არ ჩაითვალა.')
    rows = []
    for section in soup.select('section'):
        heading = text(section.find_previous_sibling('p', class_='color-2'))
        if not heading:
            parent = section.find_parent('div')
            heading = text(parent.find('p', class_='color-2')) if parent else ''
        holder = section.select_one('div[class*="obsolete"]')
        if holder is None:
            continue
        a = holder.select_one('a[href*="files.php"]')
        params = parse_qs(urlparse(a['href']).query) if a else {}
        flag = re.search(r'obsolete(\d+)', ' '.join(holder.get('class', [])))
        name = text(a) if a else text(holder)
        if not name:
            continue
        rows.append([number, heading, name, params.get('file', [''])[0],
                     dt(text(section.select_one('.date'))), flag[1] if flag else '',
                     urljoin(BASE, a['href']) if a else ''])
    return int(match[1]), int(match[2]), rows


def col_name(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result

def write_xlsx(path, sheets):
    """Portable OOXML writer: typed money/dates, filters, frozen headers."""
    prepared = []
    for name, headers, data in sheets:
        counts = [max([1] + [(len(str(row[c])) + 32766) // 32767
                             for row in data if c < len(row) and isinstance(row[c], str)])
                  for c in range(len(headers))]
        expanded_headers = [header if count == 1 else f'{header} — ნაწილი {part + 1}'
                            for header, count in zip(headers, counts) for part in range(count)]
        expanded_data = []
        for row in data:
            expanded = []
            for c, count in enumerate(counts):
                value = row[c] if c < len(row) else None
                expanded.extend([value] if count == 1 else
                                [value[p*32767:(p+1)*32767] if isinstance(value, str) else
                                 (value if p == 0 else None) for p in range(count)])
            expanded_data.append(expanded)
        prepared.append((name, expanded_headers, expanded_data))
    sheets = prepared
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    ET.register_namespace('', ns)
    def tag(name):
        return '{' + ns + '}' + name
    def xml(node):
        return ET.tostring(node, encoding='utf-8', xml_declaration=True)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        types = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
        for ext, kind in [('rels', 'application/vnd.openxmlformats-package.relationships+xml'), ('xml', 'application/xml')]:
            ET.SubElement(types, 'Default', Extension=ext, ContentType=kind)
        for part, kind in [('/xl/workbook.xml', 'sheet.main'), ('/xl/styles.xml', 'styles')]:
            ET.SubElement(types, 'Override', PartName=part, ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.' + kind + '+xml')
        workbook = ET.Element(tag('workbook'))
        sheet_nodes = ET.SubElement(workbook, tag('sheets'))
        rels = ET.Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
        for i, (name, headers, data) in enumerate(sheets, 1):
            ET.SubElement(sheet_nodes, tag('sheet'), name=name, sheetId=str(i), attrib={'{' + relns + '}id': f'rId{i}'})
            ET.SubElement(rels, 'Relationship', Id=f'rId{i}', Type=relns + '/worksheet', Target=f'worksheets/sheet{i}.xml')
            ET.SubElement(types, 'Override', PartName=f'/xl/worksheets/sheet{i}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            sheet = ET.Element(tag('worksheet'))
            view = ET.SubElement(ET.SubElement(sheet, tag('sheetViews')), tag('sheetView'), workbookViewId='0')
            ET.SubElement(view, tag('pane'), ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
            cols = ET.SubElement(sheet, tag('cols'))
            for c, header in enumerate(headers, 1):
                width = 55 if any(w in header for w in ['აღწერა', 'ბმული', 'დოკუმენტი', 'მოცულობა', 'წყაროს']) else 27
                ET.SubElement(cols, tag('col'), min=str(c), max=str(c), width=str(width), customWidth='1')
            sheet_data = ET.SubElement(sheet, tag('sheetData'))
            for r, values in enumerate([headers] + data, 1):
                row = ET.SubElement(sheet_data, tag('row'), r=str(r), ht='48' if r == 1 else '42', customHeight='1')
                for c, value in enumerate(values, 1):
                    if value is None:
                        continue
                    cell = ET.SubElement(row, tag('c'), r=f'{col_name(c)}{r}')
                    if r == 1:
                        cell.set('s', '1')
                    if isinstance(value, (datetime, date)):
                        cell.set('s', '3')
                        serial = (datetime.combine(value, datetime.min.time()) if isinstance(value, date) and not isinstance(value, datetime) else value) - datetime(1899, 12, 30)
                        ET.SubElement(cell, tag('v')).text = str(serial.total_seconds() / 86400)
                    elif isinstance(value, (Decimal, int, float)):
                        cell.set('s', '2' if isinstance(value, (Decimal, float)) else '0')
                        ET.SubElement(cell, tag('v')).text = str(value)
                    else:
                        cell.set('t', 'inlineStr')
                        ET.SubElement(ET.SubElement(cell, tag('is')), tag('t')).text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
            ET.SubElement(sheet, tag('autoFilter'), ref=f'A1:{col_name(len(headers))}{len(data)+1}')
            z.writestr(f'xl/worksheets/sheet{i}.xml', xml(sheet))
        ET.SubElement(rels, 'Relationship', Id='rStyles', Type=relns + '/styles', Target='styles.xml')
        z.writestr('xl/workbook.xml', xml(workbook))
        z.writestr('xl/_rels/workbook.xml.rels', xml(rels))
        z.writestr('[Content_Types].xml', xml(types))
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/styles.xml', '''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="dd.mm.yyyy hh:mm"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')

def progress(stage, current=0, total=None):
    callback = globals().get('on_progress')
    if callback:
        callback(stage, current, total)


def main():
    payload = dict(action='ssp_list', search='yez', ssp_status='0', assp='', ssp_agr_status='0',
                   ssp_shems_id=ORG_ID, aorg_a=ORG, ssp_mimc_id='', ssp_shems_org='', aorg_b='',
                   ssp_purchase_type='0', ssp_basecode='0', ssp_codes='', ssp_date_from=DATE_FROM,
                   ssp_date_tlll=DATE_TO, ssp_amount_from='', ssp_amount_to='', ssp_currency='0')
    collections = [[] for _ in range(8)]
    with make_session(report=print) as session:
        session.headers.update({'Referer': BASE + '?lang=ge'})
        session.get(BASE, params={'lang': 'ge'}, timeout=90).raise_for_status()
        total, current, last, candidates = read_list(request(session, payload, post=True))
        if current != 1:
            raise ValueError('საწყისი ძიება პირველი გვერდი არ არის')
        print(f'სიის გვერდი 1/{last} — {len(candidates)} ჩანაწერი', flush=True)
        progress('სიის მიღება', 1, last)
        for page in range(2, last + 1):
            count, actual_page, actual_last, rows = read_list(request(session, {'action': 'ssp_list', 'ssp_page': page}))
            if (count, actual_page, actual_last) != (total, page, last):
                raise ValueError('CMR სიის გვერდი ან შედეგების რაოდენობა შეიცვალა; გაიმეორეთ გაშვება')
            candidates.extend(rows)
            print(f'სიის გვერდი {page}/{last}', flush=True)
            progress('სიის მიღება', page, last)
        if len(candidates) != total or len({r[0] for r in candidates}) != total:
            raise ValueError('CMR სიის რაოდენობა არ ემთხვევა ან განმეორებული ID აღმოჩნდა')
        progress('მონაცემების მიღება', 0, total)
        def process(session, candidate):
            ssp_id, number, registered = candidate
            detail = parse_detail(request(session, {'action': 'view', 'ssp_id': ssp_id}), ssp_id, number, registered)
            total_docs, actual_page, docs = parse_typed(request(session, {'action': 'typed_docs_paged', 'ssp_id': ssp_id, 'typed_files_type': '0', 'page': 1}), number)
            if actual_page != 1:
                raise ValueError(f'{number}: დოკუმენტების პირველი გვერდი არასწორია')
            # Count all source records, including obsolete records without URLs.
            page_signatures = {tuple(tuple(r) for r in docs)}
            page = 1
            while len(docs) < total_docs:
                page += 1
                new_total, actual_page, batch = parse_typed(request(session, {'action': 'typed_docs_paged', 'ssp_id': ssp_id, 'typed_files_type': '0', 'page': page}), number)
                signature = tuple(tuple(r) for r in batch)
                if new_total != total_docs or actual_page != page or not batch or signature in page_signatures:
                    raise ValueError(f'{number}: თანმდევი დოკუმენტების გვერდები გადასამოწმებელია')
                page_signatures.add(signature)
                docs.extend(batch)
            if len(docs) != total_docs:
                raise ValueError(f'{number}: თანმდევი დოკუმენტების რაოდენობა არ ემთხვევა')
            return detail, docs
        for index, (detail, docs) in enumerate(parallel_map(process, candidates, session), 1):
            for target, rows in zip(collections[:7], detail):
                target.extend([rows] if target is collections[0] else rows)
            collections[7].extend(docs)
            print(f'{index}/{total}: {detail[0][0]} — {len(docs)} თანმდევი ფაილი', flush=True)
            progress('მონაცემების მიღება', index, total)
    registry, amounts, cpvs, funding, bases, deeds, attachments, typed = collections
    sheets = [
        ('CMR რეესტრი', ['CMR ნომერი', 'სისტემური ID', 'რეგისტრაციის თარიღი', 'სტატუსი', 'შესყიდვის ტიპი', 'მიმწოდებელი', 'მიმწოდებლის კოდი', 'დოკუმენტის ტიპი', 'ხელშეკრულების ღირებულება', 'ვალუტა', 'სულ ნაჩვენები გადახდები', PERIOD + ' წლების გადახდები', 'ხელშეკრულების შესრულების ტიპი', 'ძირითადი CPV', 'დეტალური CPV', 'პერიოდთან კავშირი', 'შემსყიდველის კოდი', 'შემსყიდველის დასახელება', 'შემსყიდველის სისტემური ID'], registry),
        ('თანხების ჩანაწერები', ['CMR ნომერი', 'ჩანაწერის რიგი', 'ჩანაწერის ტიპი', 'წყაროს აღწერა', 'წყაროს თარიღი', 'თანხა', 'ვალუტა', 'წელი', PERIOD + ' პერიოდშია'], amounts),
        ('CPV განაწილება', ['CMR ნომერი', 'CPV კოდი', 'განაწილებული თანხა'], cpvs),
        ('დაფინანსება', ['CMR ნომერი', 'დაფინანსების წყარო', 'წილი (1 = 100%)'], funding),
        ('შესყიდვის საფუძვლები', ['CMR ნომერი', 'საფუძველი'], bases),
        ('დოკუმენტის რეკვიზიტები', ['CMR ნომერი', 'რიგი', 'დოკუმენტის სახეობა', 'დოკუმენტის ნომერი', 'დოკუმენტის თარიღი', 'მოქმედების დასაწყისი', 'მოქმედების დასასრული'], deeds),
        ('მიმაგრებული ფაილები', ['CMR ნომერი', 'დასახელება', 'ფაილის ID', 'ატვირთვის დრო', 'obsolete კოდი', 'ბმული'], attachments),
        ('თანმდევი დოკუმენტები', ['CMR ნომერი', 'კატეგორია', 'დასახელება', 'ფაილის ID', 'ატვირთვის დრო', 'obsolete კოდი', 'ბმული'], typed),
        ('პარამეტრები', ['პარამეტრი', 'მნიშვნელობა'], [
            ['ორგანიზაცია', ORG], ['საიდენტიფიკაციო კოდი', ORG_CODE], ['როლი', 'შემსყიდველი'],
            ['პერიოდი', DATE_FROM + '–' + DATE_TO], ['ამოღების დრო', datetime.now()], ['წყარო', ENDPOINT],
            ['CMR რაოდენობა', total], ['სიის გვერდები', last], ['შესყიდვის ტიპის ფილტრი', 'ყველა ტიპი'],
            ['მოცულობა', 'ყველა ჩანაწერი, რომელიც საიტმა მოცემული თარიღის ფილტრით დააბრუნა; პერიოდის კავშირი ცალკე მოწმდება.'],
            ['სისრულის ზღვარი', 'უფრო ადრე დადებული ხელშეკრულებების არჩეული პერიოდის გადახდების სრული დაფარვა ამ ძიებით დადასტურებული არ არის.'],
            ['თარიღები', 'თანხების თარიღები ინახება როგორც წყაროს თარიღები; გადახდის საბანკო თარიღი დოკუმენტთან უნდა გადამოწმდეს.'],
            ['ცვლილებები', 'თანმდევი ფაილების კატეგორია ცვლილების დოკუმენტს გამოყოფს; PDF-ის შიგთავსი არ მუშავდება.'],
            ['მონაცემთა შენარჩუნება', 'ერთნაირი თარიღისა და თანხის დამოუკიდებელი ჩანაწერები შენარჩუნებულია.'],
            ['ვალუტა', 'ჯამები ითვლება ჩანაწერის ხელშეკრულების ვალუტაში; სხვადასხვა ვალუტა ერთმანეთს არ ემატება.']])]
    output = ROOT / ('cmr_registry_' + ORG_CODE + '_' + PERIOD + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.xlsx')
    write_xlsx(output, sheets)
    print(f'დასრულებულია: {total} CMR; Excel: {output}')
    return output


if __name__ == '__main__':
    try:
        main()
    except (requests.RequestException, ValueError, OSError) as error:
        print(f'შეცდომა: {error}\nExcel არ შექმნილა. გაიმეორეთ გაშვება ან გამოგზავნეთ შეცდომა.', file=sys.stderr)
        sys.exit(1)
