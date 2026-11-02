import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import update_jobs as m

SOURCE={'id':'aoshearman','firm':'A&O Shearman','url':'https://careers.aoshearman.com/de/country/3392/2921044/2/1','allowedHosts':['careers.aoshearman.com'],'adapter':'html'}
STAMP='2026-11-02T12:00:00Z'
def page(title='Wiss. Mitarbeit Arbeitsrecht',n=1,extra=''):
 return f'<main><h1>2 Stellenangebote</h1><ul><li><a href="/de/stellenbeschreibung/frankfurt/arbeitsrecht/3392/{100+n}">{title}</a><p>Frankfurt am Main</p></li></ul>{extra}</main><footer>Düsseldorf Hamburg München</footer>'
class MonitorTests(unittest.TestCase):
 def test_titles(self):
  for title in ['Wissenschaftlicher Mitarbeiter (m/w/d)','Wissenschaftliche Mitarbeiterin','Wiss. Mitarbeit','Praktikum / Referendariat / Wiss. Mitarbeit','Wissenschafltiche Mitarbeit Litigation','Research Assistant','Wissenschaftliche:r Mitarbeiter:in']:
   self.assertRegex(title,m.SCI)
  self.assertIsNone(m.SCI.search('Associate Corporate M&A'))
 def test_location_and_pagination(self):
  rows,nxt,ads,total=m.parse_html(page(extra='<a href="/de/country/3392/2921044/2/2" aria-label="Nächste Seite">2</a>'),SOURCE['url'])
  self.assertEqual(rows[0]['cities'],['Frankfurt']);self.assertEqual(total,2);self.assertEqual(len(nxt),1)
 def test_multiple_locations_not_inferred_from_footer(self):
  rows,*_=m.parse_html(page().replace('Frankfurt am Main</p>','Frankfurt am Main / München</p>'),SOURCE['url'])
  self.assertEqual(rows[0]['cities'],['Frankfurt','München'])
 def test_xml(self):
  src={'jobBase':'https://example.jobs.personio.de'}
  rows=m.parse_personio('<workzag-jobs><position><id>12</id><name>Wissenschaftlicher Mitarbeiter</name><office>Düsseldorf</office></position><position><id>13</id><name>Associate</name></position></workzag-jobs>',src)
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['cities'],['Düsseldorf'])
  with self.assertRaises(ValueError):m.parse_personio('<!DOCTYPE test><workzag-jobs/>',src)
 def test_canonical_urls(self):
  self.assertEqual(m.stable_id('https://careers.aoshearman.com/de/stellenbeschreibung/x/3392/123?utm_source=a'),m.stable_id('https://careers.aoshearman.com/en/job/y/3392/123'))
  self.assertEqual(m.canonical('https://arqis.jobs.personio.com/job/123?language=en'),m.canonical('https://arqis.jobs.personio.de/job/123?language=de'))
  self.assertFalse(m.allowed_url('https://careers.aoshearman.com.evil.test/job/',SOURCE['allowedHosts']))
  self.assertFalse(m.allowed_url('http://careers.aoshearman.com/job/',SOURCE['allowedHosts']))
 def test_crawl_all_pages(self):
  first=page(extra='<a rel="next" href="/de/country/3392/2921044/2/2">Weiter</a>');second=page('Wiss. Mitarbeit Corporate M&amp;A',2)
  def get(_,url):return (second if url.endswith('/2/2') else first),url
  with patch.object(m.Fetcher,'get',get):rows,health=m.crawl(SOURCE,{},STAMP)
  self.assertEqual(len(rows),2);self.assertTrue(health['complete']);self.assertEqual(health['status'],'ok')
 def test_partial_page_retains_finds(self):
  def get(_,url):
   if url.endswith('/2/2'):raise m.Blocked('blocked')
   return page(extra='<a rel="next" href="/de/country/3392/2921044/2/2">Weiter</a>'),url
  with patch.object(m.Fetcher,'get',get):rows,health=m.crawl(SOURCE,{},STAMP)
  self.assertEqual(len(rows),1);self.assertFalse(health['complete'])
 def test_empty_javascript_is_not_no_jobs(self):
  with patch.object(m.Fetcher,'get',lambda _,url:('<div id="app"></div>',url)):rows,h=m.crawl(SOURCE,{},STAMP)
  self.assertEqual(h['status'],'manual');self.assertFalse(h['complete'])
 def test_merge_preserves_status_and_no_false_closure(self):
  j=m.make_job({'url':'https://careers.aoshearman.com/de/stellenbeschreibung/frankfurt/x/3392/123','title':'Wiss. Mitarbeit','cities':['Frankfurt']},SOURCE,STAMP,{})
  prev={'jobs':[j]};blocked={'id':SOURCE['id'],'status':'error','complete':False}
  self.assertEqual(m.merge(prev,[],[blocked],STAMP)['jobs'][0]['availability'],'seen')
  ok={**blocked,'status':'ok','complete':True}
  one=m.merge(prev,[],[ok],STAMP);self.assertEqual(one['jobs'][0]['availability'],'seen')
  two=m.merge(one,[],[ok],STAMP);self.assertEqual(two['jobs'][0]['availability'],'not_seen');self.assertNotEqual(two['jobs'][0]['category'],'closed')
  new=m.make_job({'url':j['url'],'title':'Wiss. Mitarbeit geändert','cities':['Frankfurt']},SOURCE,STAMP,{})
  again=m.merge(two,[new],[ok],STAMP)['jobs'][0];self.assertEqual(again['id'],j['id']);self.assertEqual(again['availability'],'seen')
 def test_blocked_robots(self):
  def raw(_,url):
   if url.endswith('robots.txt'):return 'User-agent: *\nDisallow: /',url
   raise AssertionError('Must not request disallowed page')
  with patch.object(m.Fetcher,'raw',raw):
   with self.assertRaises(m.Blocked):m.Fetcher(SOURCE).get(SOURCE['url'])
if __name__=='__main__':unittest.main()
