import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
import sites

class ExtractionTests(unittest.TestCase):
    def test_fragment_and_identifier(self):
        self.assertEqual(sites.clean_url('https://example.com/book_31001562420/#'), 'https://example.com/book_31001562420/')
    def test_bad_scheme(self):
        with self.assertRaises(sites.SiteError): sites.clean_url('file:///etc/passwd')
    def test_credentials(self):
        with self.assertRaises(sites.SiteError): sites.clean_url('https://user:password@example.com')
    def test_index(self):
        soup = BeautifulSoup('<a href="/a">Capítulo 1</a><a href="/b">Capítulo 2</a><a href="/a">Capítulo 1</a>', 'html.parser')
        self.assertEqual(sites.links(soup,'https://example.com/book'),['https://example.com/a','https://example.com/b'])
    def test_index_selector(self):
        soup = BeautifulSoup('<div class="toc"><a href="/a">Leer</a></div>', 'html.parser')
        self.assertEqual(sites.links(soup,'https://example.com/book','.toc a'),['https://example.com/a'])
    def test_next(self):
        soup = BeautifulSoup('<a rel="next" href="/b">Next</a>', 'html.parser')
        self.assertEqual(sites.next_url(soup,'https://example.com/a'),'https://example.com/b')
    def test_next_cross_domain(self):
        soup = BeautifulSoup('<a rel="next" href="https://other.com/b">Next</a>','html.parser')
        self.assertIsNone(sites.next_url(soup,'https://example.com/a'))
    def test_text(self):
        soup = BeautifulSoup('<h1>Prueba</h1><article><nav>MENU</nav><p>'+('Una historia interesante. '*30)+'</p></article>','html.parser')
        title, paragraphs = sites.text(soup)
        self.assertEqual(title,'Prueba')
        self.assertNotIn('MENU',' '.join(paragraphs))
    def test_short(self):
        with self.assertRaises(sites.SiteError): sites.text(BeautifulSoup('<main>Solo índice</main>','html.parser'))
    def test_missing_selector(self):
        with self.assertRaises(sites.SiteError): sites.text(BeautifulSoup('<article></article>','html.parser'),'.missing')
    def test_analyze_mocked(self):
        soup = BeautifulSoup('<article><p>'+('Texto de prueba. '*40)+'</p></article>','html.parser')
        with patch.object(sites,'page',return_value=(soup,'https://example.com/chapter')):
            config, title, preview = sites.analyze({'url':'https://example.com/book','urls':'https://example.com/chapter'})
        self.assertEqual(config['urls'],['https://example.com/chapter'])
        self.assertGreater(len(preview),250)

if __name__ == '__main__': unittest.main()
