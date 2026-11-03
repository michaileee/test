"""Public procurement tender registry. Python 3.10+, requests, beautifulsoup4.
Run: python tender_registry.py
XLSX output uses Python's standard library; no Excel package is required.
"""
import json
import re
import sys
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET

import requests
from performance import ParallelRequests, make_session
from bs4 import BeautifulSoup

BASE = 'https://tenders.procurement.gov.ge/public/'
ORG = 'აფხაზეთის ავტონომიური რესპუბლიკის საინფორმაციო ანალიტიკური ცენტრი'
ROOT = Path(__file__).resolve().parent
ORG_ID = '80466'
ORG_CODE = '405384542'
DATE_FROM = '01.01.2024'
DATE_TO = '31.12.2025'
PERIOD = '2024–2025'



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


def after(value, label):
    match = re.search(re.escape(label) + r'\s*([^\n]+)', value)
    return match[1].strip() if match else ''


def get_soup(session, params):
    response = session.get(BASE + 'library/controller.php', params=params,
                           headers={'Referer': BASE + '?lang=ge'}, timeout=60)
    response.raise_for_status()
    response.encoding = 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def parse_tender(row, main_soup, contract_soup):
    app_id = row['id'].removeprefix('A')
    table = main_soup.select_one('#app_main #print_area table.with-label')
    agency = contract_soup.select_one('#agency_docs')
    if table is None or agency is None:
        raise ValueError(f'{app_id}: მონაცემების მოსალოდნელი ბლოკი ვერ მოიძებნა')
    fields = {}
    for tr in table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            fields[text(cells[0])] = text(cells[1])
    number = fields.get('განცხადების ნომერი', '')
    if not number:
        raise ValueError(f'{app_id}: ტენდერის ნომერი ვერ მოიძებნა')
    cpv = [text(li) for li in table.select('li')]
    description = ' / '.join(text(n) for n in table.select('.blabla'))
    for n in agency.select('script, style, [id^="dialog_modal_"]'):
        n.decompose()
    blocks = agency.find_all('div', class_='ui-state-highlight', recursive=False)
    contracts, changes, payments, documents = [], [], [], []
    current_amount = paid = None
    contract_status = text(agency.select_one('[class^="agrfg"]'))
    for block in blocks:
        block_text = block.get_text('\n', strip=True)
        if 'ფაქტობრივი გადახდები' in block_text:
            current_amount = money(after(block_text, 'ხელშეკრულების თანხა:'))
            paid = money(after(block_text, 'გადახდილი თანხა:'))
            for tr in block.select('tbody tr'):
                cells = tr.find_all('td', recursive=False)
                if len(cells) != 5:
                    continue
                source = text(cells[0].select_one('span'))
                year, quarter = text(cells[1]), text(cells[2])
                payments.append([number, money(text(cells[0])), int(year) if year.isdigit() else year,
                                 int(quarter) if quarter.isdigit() else quarter, dt(text(cells[3])), source])
        elif 'ნომერი/თანხა:' in block_text:
            is_change = 'ხელშეკრულების ცვლილება' in block_text
            for tr in block.select('table tr'):
                cells = tr.find_all('td', recursive=False)
                if not cells or 'ნომერი/თანხა:' not in text(cells[0]):
                    continue
                cell = cells[0]
                value = cell.get_text('\n', strip=True)
                raw_number = after(value, 'ნომერი/თანხა:').split('/')[0].strip()
                # Source sometimes concatenates the number and contract date.
                normalized_number = re.sub(r'\d{2}\.\d{2}\.\d{4}$', '', raw_number).strip()
                amount = money(text(cell.select_one('.convertme')))
                period = re.search(r'ხელშეკრულება ძალაშია:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})', value)
                start, end = (dt(period[1]), dt(period[2])) if period else (None, None)
                link = tr.find('a', href=re.compile(r'library/(?:files|contract)\.php'))
                url = urljoin(BASE, link['href']) if link else ''
                if is_change:
                    change_name = text(cell.find('u'))
                    change_date = dt(after(value, 'ხელშეკრულების ცვლილების თარიღი:'))
                    changes.append([number, change_name, normalized_number, change_date,
                                    amount, start, end, url])
                else:
                    supplier = text(cell.find('strong'))
                    signed = dt(after(value, 'ხელშეკრულების თარიღი:'))
                    contracts.append([number, normalized_number, supplier, signed,
                                      amount, start, end, url])
    seen = set()
    for a in agency.find_all('a', href=True):
        if not re.search(r'library/(?:files|contract)\.php', a['href']):
            continue
        url = urljoin(BASE, a['href'])
        if url in seen:
            continue
        seen.add(url)
        tr = a.find_parent('tr')
        metadata = text(tr.select_one('td.date')) if tr else ''
        in_docs = a.find_parent('table', id='last_docs') is not None
        documents.append([number, 'შესრულების დოკუმენტი' if in_docs else 'ხელშეკრულება / ცვლილება',
                          text(a), dt(metadata), metadata, url])
    for tr in agency.select('#last_docs tbody tr'):
        cell = tr.select_one('td[class*="doctype"], td[class*="obsolete"]')
        if cell is None or cell.select_one('a[href]') or not text(cell):
            continue
        metadata = text(tr.select_one('td.date'))
        documents.append([number, 'შესრულების დოკუმენტი — ბმულის გარეშე',
                          text(cell), dt(metadata), metadata, ''])
    # Unsuccessful, cancelled or ongoing tenders can have no contract.
    # Preserve the tender and leave contract columns blank.
    payment_sum = sum((p[1] for p in payments if p[1] is not None), Decimal('0'))
    check = ('ხელშეკრულება არ არის მითითებული' if not contracts else
             'გადახდების ჯამი ემთხვევა' if paid is not None and payment_sum == paid else
             'გადახდები გადასამოწმებელია')
    status_text = row.get_text(' ', strip=True)
    participants = re.search(r'მონაწილეთა რაოდენობა\s*-\s*(\d+)', status_text)
    main_row = [number, fields.get('შესყიდვის ტიპი', ''), fields.get('შესყიდვის სტატუსი', ''),
                contract_status, dt(fields.get('შესყიდვის გამოცხადების თარიღი', '')),
                dt(fields.get('წინადადებების მიღება იწყება', '')),
                dt(fields.get('წინადადებების მიღება მთავრდება', '')),
                fields.get('შესყიდვის კატეგორია', ''), '; '.join(cpv), description,
                money(fields.get('შესყიდვის სავარაუდო ღირებულება', '')),
                current_amount, paid, int(participants[1]) if participants else None,
                fields.get('წინადადება წარმოდგენილი უნდა იყოს', ''),
                fields.get('მოწოდების ვადა', ''), fields.get('შესყიდვის რაოდენობა ან მოცულობა', ''),
                money(fields.get('შეთავაზების ფასის კლების ბიჯი', '')),
                money(fields.get('გარანტიის ოდენობა', '')),
                fields.get('გარანტიის მოქმედების ვადა', ''),
                fields.get('დამატებითი ინფორმაცია', ''), check,
                BASE + '?go=' + app_id + '&lang=ge']
    main_row.extend([ORG_CODE, fields.get('შემსყიდველი', ''), ORG_ID])
    return main_row, contracts, changes, payments, documents


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


def file_info(a):
    url = urljoin(BASE, a['href'])
    params = parse_qs(urlparse(url).query)
    return params.get('file', params.get('go', ['']))[0], url


def parse_conditions(soup, number):
    root = soup.select_one('#app_docs')
    if root is None:
        raise ValueError(f'{number}: სატენდერო დოკუმენტაციის პასუხი ვერ ამოიცნო')
    conditions, files = [], []
    seen = set()
    for section in root.select('section.question'):
        section_id = section.get('id', '')
        title = text(section.select_one('.q > span'))
        group = section.find_parent('div')
        heading = text(group.select_one('p.color-1 > strong')) if group else ''
        answer = section.select_one('.a')
        if answer:
            for hidden in answer.select('.hst-blk, script, style'):
                hidden.decompose()
        content = answer.get_text('\n', strip=True) if answer else ''
        conditions.append([number, section_id, heading, title, content])
        for a in section.select('a[href]'):
            if 'library/files.php' not in a['href']:
                continue
            file_id, url = file_info(a)
            if (section_id, url) in seen:
                continue
            seen.add((section_id, url))
            holder = a.find_parent(class_=re.compile(r'^obsolete'))
            classes = ' '.join(holder.get('class', [])) if holder else ''
            flag = re.search(r'obsolete(\d+)', classes)
            files.append([number, section_id, text(a), file_id, flag[1] if flag else '', url])
    # Preserve any attachments outside the structured question sections.
    for a in root.select('a[href]'):
        if a.find_parent('section', class_='question') or 'library/files.php' not in a['href']:
            continue
        file_id, url = file_info(a)
        if ('', url) not in seen:
            seen.add(('', url))
            files.append([number, '', text(a), file_id, '', url])
    for holder in root.select('[class*="obsolete"]'):
        if holder.select_one('a[href]') or not text(holder):
            continue
        flag = re.search(r'obsolete(\d+)', ' '.join(holder.get('class', [])))
        if not flag:
            continue
        section = holder.find_parent('section', class_='question')
        files.append([number, section.get('id', '') if section else '',
                      text(holder), '', flag[1], ''])
    return conditions, files


def parse_bids(soup, number):
    root = soup.select_one('#app_bids')
    if root is None:
        raise ValueError(f'{number}: შეთავაზებების პასუხი ვერ ამოიცნო')
    bids = []
    for tr in root.select('tbody tr[id]'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 4:
            continue
        profile = cells[0].find('a', onclick=re.compile('ShowProfile'))
        history = cells[3].find('a', onclick=re.compile('ShowBidHistory'))
        org_match = re.search(r'ShowProfile\((\d+)\)', profile.get('onclick', '')) if profile else None
        bid_match = re.search(r'ShowBidHistory\(\s*\d+\s*,\s*(\d+)\s*\)', history.get('onclick', '')) if history else None
        if not org_match or not bid_match:
            raise ValueError(f'{number}: პრეტენდენტის ID ან შეთავაზების ID ვერ მოიძებნა')
        count = re.search(r'\[(\d+)\]', text(cells[3]))
        name = text(cells[0])
        bids.append([number, org_match[1], bid_match[1], name,
                     money(text(cells[2])), dt(text(cells[2].select_one('.date'))),
                     money(text(cells[1])), dt(text(cells[1].select_one('.date'))),
                     int(count[1]) if count else None])
    return bids


def parse_history(soup, bid):
    table = soup.select_one('table.ktable')
    if table is None:
        raise ValueError(f'{bid[0]} / {bid[2]}: შეთავაზების ისტორია ვერ მოიძებნა')
    rows = []
    for tr in table.select('tbody tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            rows.append([bid[0], bid[1], bid[2], money(text(cells[0])), dt(text(cells[1]))])
    if bid[8] is not None and len(rows) != bid[8]:
        raise ValueError(f'{bid[0]} / {bid[2]}: ისტორიის რაოდენობა არ ემთხვევა ({len(rows)} / {bid[8]})')
    return rows


def parse_technical(soup, number, bids):
    table = soup.select_one('#tdocs')
    if table is None:
        raise ValueError(f'{number}: ტექნიკური დოკუმენტაციის ცხრილი ვერ მოიძებნა')
    rows = []
    for tr in table.select('tbody tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 3:
            continue
        name = text(cells[0])
        candidates = [b for b in bids if b[3] == name]
        org_id, bid_id = (candidates[0][1], candidates[0][2]) if len(candidates) == 1 else ('', '')
        a = cells[1].find('a', href=True)
        file_id, url = file_info(a) if a else ('', '')
        filename = text(a) if a else text(cells[1])
        if not filename:
            continue
        flag = re.search(r'obsolete(\d+)', ' '.join(cells[1].get('class', [])))
        if flag is None:
            holder = cells[1].select_one('[class*="obsolete"]')
            flag = re.search(r'obsolete(\d+)', ' '.join(holder.get('class', []))) if holder else None
        rows.append([number, org_id, bid_id, name, filename, file_id, dt(text(cells[2])),
                     'სახელით ცალსახად დაკავშირებულია' if org_id else 'კავშირი გადასამოწმებელია', url])
        rows[-1].append(flag[1] if flag else '')
    return rows


def parse_results(soup, number):
    table = soup.select_one('#reports')
    if table is None:
        raise ValueError(f'{number}: შედეგების დოკუმენტების ცხრილი ვერ მოიძებნა')
    rows = []
    for tr in table.select('tbody tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 3:
            continue
        a = cells[1].find('a', href=True)
        file_id, url = file_info(a) if a else ('', '')
        filename = text(a) if a else text(cells[1])
        if not filename:
            continue
        metadata = text(cells[2])
        classes = ' '.join(cells[1].get('class', []))
        kind, obsolete = re.search(r'doctype(\d+)', classes), re.search(r'obsolete(\d+)', classes)
        rows.append([number, filename, file_id, kind[1] if kind else '', obsolete[1] if obsolete else '',
                     dt(metadata), metadata.split('::', 1)[1].strip() if '::' in metadata else '', url])
    return rows


def parse_profile(soup, org_id):
    root = soup.select_one('#profile_dialog')
    table = root.select_one('table.with-label') if root else None
    if table is None:
        raise ValueError(f'{org_id}: პრეტენდენტის პროფილი ვერ მოიძებნა')
    fields = {}
    first_value = None
    for tr in table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            if first_value is None:
                first_value = cells[1]
            fields[text(cells[0])] = text(cells[1])
    name = text(first_value.find('strong')) if first_value else ''
    form = text(first_value.find('label')) if first_value else ''
    city, address = fields.get('ქალაქი/დაბა/სოფელი', ''), fields.get('მისამართი', '')
    if city == address:
        city = ''
    website = fields.get('ვებ-გვერდი', '')
    fax = fields.get('ფაქსი', '')
    if website in ('http://', 'https://'):
        website = ''
    if fax in ('+995', '995'):
        fax = ''
    profile = [org_id, name, form, fields.get('საიდენტიფიკაციო კოდი', ''), fields.get('ქვეყანა', ''),
               city, address, fields.get('ტელეფონი', ''), fax, fields.get('ელ-ფოსტა', ''), website]
    contacts, cpvs = [], []
    for tr in root.select('tbody#c tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 3:
            continue
        person = text(cells[0].find('strong'))
        role = text(cells[0])
        if role.startswith(person):
            role = role[len(person):].strip()
        contacts.append([org_id, tr.get('id', ''), person, role, text(cells[1]), text(cells[2])])
    for li in root.select('ul li'):
        code = text(li.find('strong'))
        if re.fullmatch(r'\d{8}', code):
            cpvs.append([org_id, code, text(li)[len(code):].lstrip(' -')])
    return profile, contacts, cpvs


def collect_search_rows(session, payload):
    """Initialize search once, then traverse pages in the same HTTP session."""
    def read(soup):
        table = soup.select_one('#list_apps_by_subject')
        if table is None:
            raise ValueError('ძიების ცხრილი ვერ მოიძებნა; არასრული Excel არ შექმნილა.')
        rows = table.find_all('tr', id=re.compile(r'^A\d+$'))
        pager = soup.select_one('.pager')
        if pager is None:
            return rows, len(rows), 1, 1
        match = re.search(r'(\d+)\s*ჩანაწერ.*?გვერდი\s*:?\s*(\d+)\s*/\s*(\d+)', text(pager))
        if not match:
            raise ValueError('ტენდერების გვერდების რაოდენობა ვერ შემოწმდა; არასრული Excel არ შექმნილა.')
        total, page, last = map(int, match.groups())
        if total == 0 and not rows and page in (0, 1) and last in (0, 1):
            return rows, 0, 1, 1
        if not 1 <= page <= last:
            raise ValueError('ტენდერების გვერდის ნომერი არასწორია')
        return rows, total, page, last

    response = session.post(BASE + 'library/controller.php', data=payload,
                            headers={'Referer': BASE + '?lang=ge'}, timeout=60)
    response.raise_for_status()
    response.encoding = 'utf-8'
    rows, total, page, last = read(BeautifulSoup(response.text, 'html.parser'))
    if page != 1:
        raise ValueError('ტენდერების საწყისი ძიება პირველი გვერდი არ არის')
    rows = list(rows)
    seen = {row['id'] for row in rows}
    if len(seen) != len(rows):
        raise ValueError('ძიების გვერდზე განმეორებული სისტემური ID-ებია')
    print(f'ტენდერების სიის გვერდი 1/{last} — სულ {total} ჩანაწერი')
    progress('სიის მიღება', 1, last)
    for expected_page in range(2, last + 1):
        # Site pager uses GET action=search_app&page=N, retaining filters in session.
        next_rows, next_total, current, next_last = read(get_soup(
            session, {'action': 'search_app', 'page': expected_page}))
        if (next_total, current, next_last) != (total, expected_page, last):
            raise ValueError('ტენდერების გვერდი ან შედეგების რაოდენობა შეიცვალა; გაიმეორეთ გაშვება')
        if not next_rows or any(row['id'] in seen for row in next_rows):
            raise ValueError('ტენდერების გვერდი ცარიელია ან უკვე მიღებულ ჩანაწერს იმეორებს')
        ids = {row['id'] for row in next_rows}
        if len(ids) != len(next_rows):
            raise ValueError('ძიების გვერდზე განმეორებული სისტემური ID-ებია')
        rows.extend(next_rows)
        seen.update(ids)
        print(f'ტენდერების სიის გვერდი {expected_page}/{last}')
        progress('სიის მიღება', expected_page, last)
    if len(rows) != total:
        raise ValueError(f'ტენდერების რაოდენობა არ ემთხვევა: {len(rows)} / {total}; არასრული Excel არ შექმნილა.')
    print(f'სისტემური ID {ORG_ID}: ძიებით მიღებულია {len(rows)} ტენდერი')
    return rows


def progress(stage, current=0, total=None):
    callback = globals().get('on_progress')
    if callback:
        callback(stage, current, total)


def main():
    payload = dict(action='search_app', app_t='0', search='', app_reg_id='', app_shems_id=ORG_ID,
                   org_a=ORG, app_monac_id='0', org_b='', app_particip_status_id='0', app_donor_id='0',
                   app_status='0', app_agr_status='0', app_type='0', app_basecode='0', app_codes='',
                   app_date_type='1', app_date_from=DATE_FROM, app_date_tlll=DATE_TO,
                   app_amount_from='', app_amount_to='', app_currency='2', app_pricelist='0')
    registry, contracts, changes, payments, documents = [], [], [], [], []
    conditions, condition_files, all_bids, histories, technical, results = [], [], [], [], [], []
    profiles, contacts, profile_cpvs = {}, [], []
    with make_session(report=print) as session, ParallelRequests(session) as parallel:
        session.get(BASE, params={'lang': 'ge'}, timeout=60).raise_for_status()
        rows = collect_search_rows(session, payload)
        progress('მონაცემების მიღება', 0, len(rows))
        seen = set()
        for row in rows:
            match = re.search(r"ShowApp\(\s*(\d+)\s*,\s*'[^']*'\s*,\s*\d+\s*,\s*'([^']+)'\s*\)", row.get('onclick', ''))
            if not match:
                raise ValueError(f"ID/key ვერ მოიძებნა: {row.get('id')}")
            app_id, key = match.groups()
            if app_id in seen:
                continue
            seen.add(app_id)
            params = {'app_id': app_id, 'key': key}
            actions = ['app_main', 'agr_docs', 'app_docs', 'app_bids', 'app_tdocs', 'agency_docs']
            responses = dict(zip(actions, parallel.map(
                lambda worker_session, action: get_soup(worker_session, dict(params, action=action)),
                actions)))
            detail = responses['app_main']
            agreement = responses['agr_docs']
            record, cs, ch, ps, ds = parse_tender(row, detail, agreement)
            registry.append(record)
            contracts.extend(cs); changes.extend(ch); payments.extend(ps); documents.extend(ds)
            number = record[0]
            params = {'app_id': app_id, 'key': key}
            cond, cond_files = parse_conditions(responses['app_docs'], number)
            bids = parse_bids(responses['app_bids'], number)
            conditions.extend(cond); condition_files.extend(cond_files); all_bids.extend(bids)
            technical.extend(parse_technical(responses['app_tdocs'], number, bids))
            results.extend(parse_results(responses['agency_docs'], number))
            jobs, queued_profiles = [], set(profiles)
            for bid in bids:
                jobs.append(('history', bid, {'action': 'view_bid', 'app_id': app_id, 'bid_id': bid[2]}))
                if bid[1] not in queued_profiles:
                    queued_profiles.add(bid[1])
                    jobs.append(('profile', bid[1], {'action': 'profile', 'org_id': bid[1]}))
            soups = parallel.map(lambda worker_session, job: get_soup(worker_session, job[2]), jobs)
            for (kind, target, _), soup in zip(jobs, soups):
                if kind == 'history':
                    histories.extend(parse_history(soup, target))
                else:
                    org_id = target
                    profile, contact, cpv = parse_profile(soup, org_id)
                    profiles[org_id] = profile
                    contacts.extend(contact); profile_cpvs.extend(cpv)
            print(f'{record[0]} — მიღებულია')
            progress('მონაცემების მიღება', len(registry), len(rows))
    registry.sort(key=lambda r: r[4] or datetime.min)
    headers = ['ტენდერის ნომერი', 'შესყიდვის ტიპი', 'შესყიდვის სტატუსი', 'ხელშეკრულების სტატუსი',
               'გამოცხადების თარიღი', 'წინადადებების მიღების დასაწყისი', 'წინადადებების მიღების დასასრული',
               'შესყიდვის კატეგორია', 'დეტალური CPV კოდები', 'შესყიდვის აღწერა',
               'სავარაუდო ღირებულება (GEL)', 'მიმდინარე ხელშეკრულების თანხა (GEL)',
               'სულ გადახდილი (GEL)', 'მონაწილეთა რაოდენობა', 'დღგ-ის პირობა', 'მოწოდების ვადა',
               'რაოდენობა ან მოცულობა', 'ფასის კლების ბიჯი (GEL)', 'გარანტიის თანხა (GEL)',
               'გარანტიის მოქმედების ვადა', 'დამატებითი ინფორმაცია', 'გადახდების შემოწმება', 'ტენდერის ბმული',
               'შემსყიდველის კოდი', 'შემსყიდველის დასახელება', 'შემსყიდველის სისტემური ID']
    metadata = [['ორგანიზაცია', ORG], ['საიდენტიფიკაციო კოდი', ORG_CODE], ['სისტემური ID', ORG_ID],
                ['როლი', 'შემსყიდველი'], ['პერიოდი', DATE_FROM + '–' + DATE_TO], ['ვალუტა', 'GEL'],
                ['ამოღების დრო', datetime.now()], ['წყარო', BASE],
                ['მოცულობა', 'რეგისტრაციის თარიღით მიღებული ტენდერები; CMR არ შედის; გვერდების სისრულე ჯერ დასადასტურებელია.'],
                ['თანხების მნიშვნელობა', 'საწყისი და ცვლილებების თანხები ვერსიებია და არ ჯამდება. გადახდების ფურცელზე თითოეული გადახდა ცალკე ჩანაწერია.'],
                ['ხელშეკრულების ნომერი', 'წყაროში მიწებებული თარიღი მოცილებულია; ნომერი საჭიროების შემთხვევაში PDF-თან გადაამოწმეთ.'],
                ['დოკუმენტები', 'ინახება ბმულები და მეტამონაცემები; PDF-ების შიგთავსი არ არის ამოღებული.']]
    headers.insert(1, 'მიმწოდებელი')
    suppliers = {r[0]: r[2] for r in contracts}
    for record in registry:
        record.insert(1, suppliers.get(record[0], ''))
    sheets = [('რეესტრი', headers, registry),
              ('ხელშეკრულებები', ['ტენდერის ნომერი', 'ხელშეკრულების ნომერი', 'მიმწოდებელი', 'ხელშეკრულების თარიღი', 'საწყისი თანხა (GEL)', 'ძალაშია — დასაწყისი', 'ძალაშია — დასასრული', 'დოკუმენტის ბმული'], contracts),
              ('ცვლილებები', ['ტენდერის ნომერი', 'ცვლილება', 'ხელშეკრულების ნომერი', 'ცვლილების თარიღი', 'ვერსიის თანხა (GEL)', 'ძალაშია — დასაწყისი', 'ძალაშია — დასასრული', 'დოკუმენტის ბმული'], changes),
              ('გადახდები', ['ტენდერის ნომერი', 'გადახდის თანხა (GEL)', 'გადახდის წელი', 'კვარტალი', 'გადახდის თარიღი', 'დაფინანსების წყარო'], payments),
              ('დოკუმენტები', ['ტენდერის ნომერი', 'დოკუმენტის ტიპი', 'დოკუმენტის დასახელება', 'ატვირთვის თარიღი', 'წყაროს თარიღი და ავტორი', 'დოკუმენტის ბმული'], documents),
              ('პარამეტრები', ['პარამეტრი', 'მნიშვნელობა'], metadata)]
    # Remove the duplicate upload-date column: it is already in source metadata.
    sheets[4] = ('დოკუმენტები', ['ტენდერის ნომერი', 'დოკუმენტის ტიპი', 'დოკუმენტის დასახელება', 'ატვირთვის თარიღი', 'ავტორი', 'დოკუმენტის ბმული'],
                 [[r[0], r[1], r[2], r[3], r[4].split('::', 1)[1].strip() if '::' in r[4] else '', r[5]] for r in documents])
    sheets[-1][2].extend([
        ['პროფილების დრო', 'პროფილები ამოღების მომენტის მონაცემებია და არა ისტორიული პროფილები.'],
        ['პროფილის მისამართები', 'თუ ქალაქის ველი და მისამართი იდენტურია, ქალაქის ველი ცარიელი რჩება.'],
        ['შედეგების ტიპები', 'doctype და obsolete სვეტები წყაროს კოდებია; მათი მნიშვნელობები არ არის ინტერპრეტირებული.'],
        ['ტექნიკური ფაილების კავშირი', 'პრეტენდენტის ID მიებმება მხოლოდ ტენდერის შიგნით სახელის ცალსახა დამთხვევით.'],
        ['Excel-ის გრძელი ტექსტი', '32767 სიმბოლოზე გრძელი ტექსტი რამდენიმე ნაწილად იყოფა; ნაწილის ნომერი სათაურშია.']])
    new_sheets = [
        ('სატენდერო პირობები', ['ტენდერის ნომერი', 'პუნქტის ID', 'განყოფილება', 'პუნქტის დასახელება', 'პირობის სრული ტექსტი'], conditions),
        ('სატენდერო დოკუმენტები', ['ტენდერის ნომერი', 'პუნქტის ID', 'ფაილის დასახელება', 'ფაილის ID', 'obsolete კოდი', 'ბმული'], condition_files),
        ('შეთავაზებები', ['ტენდერის ნომერი', 'პრეტენდენტის სისტემური ID', 'შეთავაზების ID', 'პრეტენდენტი', 'პირველი თანხა (GEL)', 'პირველი შეთავაზების დრო', 'ბოლო თანხა (GEL)', 'ბოლო შეთავაზების დრო', 'შეთავაზებების რაოდენობა'], all_bids),
        ('შეთავაზებების ისტორია', ['ტენდერის ნომერი', 'პრეტენდენტის სისტემური ID', 'შეთავაზების ID', 'თანხა (GEL)', 'დრო'], histories),
        ('ტექნიკური დოკუმენტები', ['ტენდერის ნომერი', 'პრეტენდენტის სისტემური ID', 'შეთავაზების ID', 'პრეტენდენტი წყაროში', 'ფაილის დასახელება', 'ფაილის ID', 'ატვირთვის დრო', 'კავშირის შემოწმება', 'ბმული', 'obsolete კოდი'], technical),
        ('შედეგების დოკუმენტები', ['ტენდერის ნომერი', 'ფაილის დასახელება', 'ფაილის ID', 'doctype კოდი', 'obsolete კოდი', 'ატვირთვის დრო', 'ავტორი', 'ბმული'], results),
        ('პრეტენდენტები', ['სისტემური ID', 'დასახელება', 'სამართლებრივი ფორმა', 'საიდენტიფიკაციო კოდი', 'ქვეყანა', 'ქალაქი — განსხვავებული ჩანაწერი', 'მისამართი', 'ტელეფონი', 'ფაქსი', 'ელფოსტა', 'ვებგვერდი'], list(profiles.values())),
        ('საკონტაქტო პირები', ['ორგანიზაციის სისტემური ID', 'კონტაქტის ID', 'სახელი', 'თანამდებობა', 'ტელეფონი', 'ელფოსტა'], contacts),
        ('პრეტენდენტების CPV', ['ორგანიზაციის სისტემური ID', 'CPV კოდი', 'აღწერა'], profile_cpvs)]
    sheets[-1:-1] = new_sheets
    output = ROOT / ('tender_registry_' + ORG_CODE + '_' + PERIOD + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.xlsx')
    write_xlsx(output, sheets)
    print(f'დასრულებულია: {len(registry)} ტენდერი, {len(payments)} გადახდა, {len(documents)} დოკუმენტის ბმული.')
    print(f'Excel: {output}')
    return output


if __name__ == '__main__':
    try:
        main()
    except (requests.RequestException, ValueError, OSError) as error:
        print(f'შეცდომა: {error}', file=sys.stderr)
        sys.exit(1)
