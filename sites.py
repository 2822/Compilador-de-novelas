"""Extracción configurable de HTML público. Sin elusión de controles de acceso."""
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup

class SiteError(ValueError):
    pass

def clean_url(value):
    if not isinstance(value, str):
        raise SiteError('La URL debe ser texto')
    p = urlsplit(value.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise SiteError('Se requiere una URL HTTP(S) sin credenciales')
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))

def page(url, config):
    url = clean_url(url)
    if config.get('renderer') == 'browser':
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SiteError('Falta Playwright. Consulta LEEME para instalar el modo navegador.')
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    tab = browser.new_page()
                    response = tab.goto(url, wait_until='domcontentloaded', timeout=45000)
                    if response and response.status >= 400:
                        raise SiteError(f'El sitio devolvió HTTP {response.status}; no se eluden restricciones.')
                    selector = config.get('content_selector')
                    if selector:
                        tab.wait_for_selector(selector, timeout=15000)
                    else:
                        tab.wait_for_timeout(2500)
                    return BeautifulSoup(tab.content(), 'html.parser'), clean_url(tab.url)
                finally:
                    browser.close()
        except SiteError:
            raise
        except Exception as e:
            raise SiteError(f'No se pudo usar Chromium: {e}. Consulta LEEME.')
    response = requests.get(url, timeout=35, headers={'User-Agent': 'Mozilla/5.0 (compatible; NovelReader/1.0)'})
    if response.status_code >= 400:
        raise SiteError(f'HTTP {response.status_code}: enlace no disponible o acceso restringido. No se eluden pagos ni bloqueos.')
    if 'html' not in response.headers.get('Content-Type', 'text/html').lower():
        raise SiteError('La respuesta no es HTML')
    return BeautifulSoup(response.content, 'html.parser'), clean_url(response.url)

def text(soup, selector=''):
    if selector:
        container = soup.select_one(selector)
        if container is None:
            raise SiteError('El selector CSS de texto no coincide')
    else:
        candidates = soup.select('.entry-content, .chapter-content, #chapter-content, .reading-content, .read-content, article, main')
        if not candidates:
            raise SiteError('No se reconoce el texto: configura un selector CSS o prueba el modo navegador.')
        container = max(candidates, key=lambda x: len(x.get_text(' ', strip=True)))
    fragment = BeautifulSoup(str(container), 'html.parser')
    for element in fragment.select('script, style, nav, header, footer, aside, iframe, ins, button, form, noscript'):
        element.decompose()
    paragraphs = [p.get_text(' ', strip=True) for p in fragment.find_all(['p', 'h2', 'h3'])]
    if not paragraphs:
        paragraphs = fragment.get_text('\n', strip=True).splitlines()
    paragraphs = [p for p in paragraphs if p]
    if sum(map(len, paragraphs)) < 250:
        raise SiteError('No hay suficiente texto: puede ser un índice, contenido corto, una vista previa o acceso restringido.')
    heading = soup.find('h1')
    return (heading.get_text(' ', strip=True) if heading else 'Capítulo'), paragraphs

def links(soup, base, selector=''):
    result = []
    for a in soup.select(selector or 'a[href]'):
        if not selector and not re.search(r'(?:cap[ií]tulo|chapter|episodio)\s*\d+', a.get_text(' ', strip=True), re.I):
            continue
        if not a.get('href'):
            continue
        try:
            url = clean_url(urljoin(base, a['href']))
            if urlsplit(url).netloc == urlsplit(base).netloc and url != clean_url(base) and url not in result:
                result.append(url)
        except SiteError:
            pass
    return result

def next_url(soup, base, selector=''):
    if selector:
        choices = soup.select(selector)
    else:
        choices = soup.select('a[rel~="next"]') or [a for a in soup.select('a[href]') if re.fullmatch(
            r'(?:cap[ií]tulo\s+)?siguiente(?:\s+cap[ií]tulo)?\s*[»›→]?|next(?:\s+chapter)?\s*[»›→]?',
            a.get_text(' ', strip=True), re.I)]
    for a in choices:
        if a.get('href'):
            try:
                target = clean_url(urljoin(base, a['href']))
                if target != clean_url(base) and urlsplit(target).netloc == urlsplit(base).netloc:
                    return target
            except SiteError:
                pass
    return None

def analyze(data):
    config = {key: str(data.get(key) or '').strip() for key in
              ('content_selector', 'index_selector', 'next_selector', 'renderer', 'title')}
    if config['renderer'] not in ('http', 'browser'):
        config['renderer'] = 'http'
    config['url'] = clean_url(data.get('url', ''))
    for key in ('content_selector', 'index_selector', 'next_selector'):
        if config[key]:
            BeautifulSoup('<a></a>', 'html.parser').select(config[key])
    manual = str(data.get('urls') or '').strip()
    if manual:
        urls = list(dict.fromkeys(clean_url(line) for line in manual.splitlines() if line.strip()))
    else:
        soup, actual = page(config['url'], config)
        urls = links(soup, actual, config['index_selector'])
        if not urls:
            text(soup, config['content_selector'])
            urls = [actual]
    if not 1 <= len(urls) <= 10000:
        raise SiteError('Se requieren entre 1 y 10000 enlaces de capítulos')
    soup, actual = page(urls[0], config)
    title, paragraphs = text(soup, config['content_selector'])
    config['urls'] = urls
    config['title'] = config['title'][:120] or 'Novela'
    return config, title, '\n'.join(paragraphs)[:2000]
