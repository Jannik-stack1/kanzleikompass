#!/usr/bin/env python3
"""Public official job feeds/pages only. No login, secrets, browser bypass or private state."""
from __future__ import annotations
import argparse,concurrent.futures,datetime as dt,hashlib,html,json,re,sys,time,unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlparse,urlunparse,parse_qsl,urlencode,unquote
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
AGENT='KanzleikompassMonitor/1.0'
LIMIT=3*1024*1024
SCI=re.compile(r'\b(?:wiss[\w:.*-]*\s*mitarb\w*|research\s+assistants?|law\s+clerks?|juristische\s+mitarbeit)',re.I)
AD=re.compile(r'/job/|/stellenbeschreibung/|/offene-stellen/|/stellenausschreibungen/(?!karrieremoeglichkeiten)|/stellenanzeigen/[^/?]+|/Stellenangebote/[^/?]+|-de-j\d+|[?&](?:yid|id|tx_jobs_list%5Boffer%5D)=\w+',re.I)
EXPIRED=re.compile(r'(?:diese|die)\s+stelle.{0,90}(?:besetzt|nicht mehr verfügbar)|job.{0,80}(?:has been filled|no longer available)|emploi.{0,90}(?:pourvu)|trabajo.{0,90}(?:llenado)',re.I)
CITIES={'Düsseldorf':['düsseldorf','dusseldorf','duesseldorf','dsseldorf'],'Frankfurt':['frankfurt am main','frankfurt'],'Köln':['köln','koeln','cologne'],'München':['münchen','munchen','muenchen','munich'],'Hamburg':['hamburg'],'Berlin':['berlin'],'Stuttgart':['stuttgart'],'Bonn':['bonn'],'Duisburg':['duisburg'],'Essen':['essen'],'Mönchengladbach':['mönchengladbach','moenchengladbach'],'Dortmund':['dortmund'],'Mannheim':['mannheim'],'Dresden':['dresden'],'Leipzig':['leipzig'],'Düsseldorf / remote':['düsseldorf / remote']}
FIELDS=[('Corporate / M&A',r'corporate|gesellschaft|m\s*[&/]\s*a|m-and-a|aktien'),('Private Equity',r'private.?equity|venture.?capital'),('Litigation / Arbitration',r'litigation|dispute|arbitration|prozess|schieds|konflikt'),('IP / IT',r'\bip\b|\bit\b|data|datenschutz|tech|digital|patent|intellectual'),('Banking / Finance',r'bank|financ|kapitalmarkt|capital.?market|investment|kredit'),('Real Estate',r'immobil|real.?estate|baurecht'),('Steuerrecht',r'steuer|\btax\b'),('Arbeitsrecht',r'arbeitsrecht|employment|hr.law'),('Kartellrecht',r'kartell|antitrust|competition'),('Öffentliches Recht',r'öffentlich|offentlich|regulatory|vergab'),('Energie',r'energ'),('Restrukturierung',r'restruktur|restructur|insolven'),('Wirtschaftsstrafrecht',r'strafrecht|white.?collar|crime')]
def now():return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def text(s):return re.sub(r'\s+',' ',html.unescape(str(s or ''))).strip()
def cities(s):
 s=unquote(text(s)).lower();return list(dict.fromkeys(c for c,variants in CITIES.items() if c!='Düsseldorf / remote' and any(re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)',s) for v in variants)))
def fields(s):return [name for name,pattern in FIELDS if re.search(pattern,s,re.I)] or ['Allgemein']
def canonical(url):
 u=urlparse(html.unescape(url));host=u.hostname or ''
 host=host.lower().removeprefix('www.').replace('.jobs.personio.com','.jobs.personio.de')
 path=unquote(u.path).rstrip('/') or '/'
 if '.myworkdayjobs.com' in host:
  req=re.search(r'((?:JR|R)[-_]?\d+(?:-\d+)?)$',path,re.I)
  if req:return host+'/job/'+req.group(1).lower().removesuffix('-1')
 if 'aoshearman.com' in host:
  job=re.search(r'/3392/(\d+)$',path)
  if job:return host+'/job/'+job.group(1)
 keep=[(k,v) for k,v in parse_qsl(u.query) if k.lower() in ['id','yid','jobid','job_id','gh_jid','tx_jobs_list[offer]']]
 return host+path+('?' + urlencode(sorted(keep)) if keep else '')
def stable_id(url):return 'live-'+hashlib.sha256(canonical(url).encode()).hexdigest()[:16]

class Node:
 def __init__(self,tag='',attrs=None,parent=None):self.tag=tag;self.attrs=dict(attrs or []);self.parent=parent;self.children=[]
 def content(self):return text(' '.join(c.content() if isinstance(c,Node) else c for c in self.children if not isinstance(c,Node) or c.tag not in ['script','style','nav','footer','header']))
 def walk(self):
  yield self
  for c in self.children:
   if isinstance(c,Node):yield from c.walk()
class DOM(HTMLParser):
 VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
 def __init__(self,body):
  super().__init__(convert_charrefs=True);self.root=Node('root');self.current=self.root;self.feed(body)
 def handle_starttag(self,tag,attrs):
  n=Node(tag,attrs,self.current);self.current.children.append(n)
  if tag not in self.VOID:self.current=n
 def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
 def handle_endtag(self,tag):
  n=self.current
  while n.parent is not None:
   if n.tag==tag:self.current=n.parent;return
   n=n.parent
 def handle_data(self,data):self.current.children.append(data)
def parse_html(body,base_url):
 dom=DOM(body);nodes=list(dom.root.walk());jobs=[];all_ads=set();next_pages=[]
 for n in nodes:
  if n.tag!='a':continue
  href=n.attrs.get('href','');title=n.content();url=urljoin(base_url,href)
  nav_text=title+' '+n.attrs.get('aria-label','')
  ao_page=bool('careers.aoshearman.com' in urlparse(base_url).netloc and re.search(r'/3392/2921044/2/\d+(?:[?#]|$)',url))
  if href and not href.startswith('#') and ('next' in n.attrs.get('rel','').lower() or re.search(r'\b(?:weiter|next|nächste(?: seite)?)\b',nav_text,re.I) or title in ['›','»'] or ao_page):next_pages.append(url)
  if not AD.search(url) or not title:continue
  all_ads.add(canonical(url))
  if not SCI.search(title):continue
  context=title;parent=n.parent
  for _ in range(5):
   if parent is None or parent.tag in ['body','main','html','root','nav','header','footer']:break
   content=parent.content()
   links={canonical(urljoin(base_url,x.attrs.get('href',''))) for x in parent.walk() if x.tag=='a' and AD.search(x.attrs.get('href',''))}
   if len(content)>1800 or len(links)>1:break
   context=content
   if parent.tag in ['tr','li','article'] and cities(context):break
   parent=parent.parent
  jobs.append({'url':url,'title':title,'cities':cities(context),'context':context})
 # Structured JobPosting data is a second source of locations, never a fabricated office list.
 for n in nodes:
  if n.tag=='script' and 'ld+json' in n.attrs.get('type',''):
   try:data=json.loads(''.join(c for c in n.children if isinstance(c,str)))
   except (ValueError,TypeError):continue
   def walk(v):
    if isinstance(v,list):
     for e in v:yield from walk(e)
    elif isinstance(v,dict):
     if 'JobPosting' in ([v.get('@type')] if isinstance(v.get('@type'),str) else v.get('@type',[])):yield v
     yield from walk(v.get('@graph',[]))
   for j in walk(data):
    title=text(j.get('title'));url=urljoin(base_url,j.get('url') or base_url)
    if not SCI.search(title):continue
    loc=j.get('jobLocation',[]);loc=loc if isinstance(loc,list) else [loc]
    places=[text(x.get('address',{}).get('addressLocality')) for x in loc if isinstance(x,dict) and isinstance(x.get('address',{}),dict)]
    jobs.append({'url':url,'title':title,'cities':list(dict.fromkeys(c for p in places for c in cities(p))),'context':text(j.get('description',''))[:1200]})
 total=re.search(r'\b(\d+)\s+(?:Stellenangebote|Jobs|offene Stellen)\b',dom.root.content(),re.I)
 return jobs,list(dict.fromkeys(next_pages)),all_ads,int(total.group(1)) if total else None

def parse_personio(body,source):
 if '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():raise ValueError('Unsichere XML-Deklaration')
 root=ET.fromstring(body)
 if root.tag.split('}')[-1].lower() not in ['workzag-jobs','jobs','positions']:raise ValueError('Unbekanntes XML-Format')
 jobs=[]
 for p in root.findall('.//position'):
  title=text(p.findtext('name'));ident=text(p.findtext('id'))
  if not SCI.search(title) or not re.fullmatch(r'\d+',ident):continue
  office=text(p.findtext('office'));context=office+' '+text(p.findtext('department'))
  jobs.append({'url':source['jobBase'].rstrip('/')+'/job/'+ident+'?language=de','title':title,'cities':cities(office),'context':context})
 return jobs

class Blocked(Exception):pass
class SafeRedirect(HTTPRedirectHandler):
 def __init__(self,allowed):self.allowed=allowed
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  if not allowed_url(newurl,self.allowed):raise Blocked('Weiterleitung zu nicht freigegebener Domain')
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def allowed_url(url,allowed):
 u=urlparse(url);return u.scheme=='https' and not u.username and not u.password and (u.hostname or '').lower().removeprefix('www.') in {h.lower().removeprefix('www.') for h in allowed}
class Fetcher:
 def __init__(self,source):self.source=source;self.allowed=source['allowedHosts'];self.robots={};self.opener=build_opener(SafeRedirect(self.allowed))
 def raw(self,url):
  if not allowed_url(url,self.allowed):raise Blocked('Nicht freigegebene Quelladresse')
  req=Request(url,headers={'User-Agent':AGENT,'Accept':'text/html,application/xml,application/json;q=0.9','Accept-Language':'de,en;q=0.6'})
  with self.opener.open(req,timeout=12) as r:
   data=r.read(LIMIT+1)
   if len(data)>LIMIT:raise ValueError('Quelle überschreitet die Größenbegrenzung')
   encoding=r.headers.get_content_charset() or 'utf-8'
   return data.decode(encoding,errors='replace'),r.geturl()
 def get(self,url):
  host=urlparse(url).netloc
  if host not in self.robots:
   robot=RobotFileParser();robot.set_url('https://'+host+'/robots.txt')
   try:body,_=self.raw(robot.url);robot.parse(body.splitlines())
   except HTTPError as e:
    if e.code in [404,410]:robot.parse([])
    else:raise Blocked('robots.txt nicht zuverlässig zugänglich')
   except Exception:raise Blocked('robots.txt konnte nicht zuverlässig geprüft werden')
   self.robots[host]=robot
  if not self.robots[host].can_fetch(AGENT,url):raise Blocked('Automatischer Abruf laut robots.txt nicht erlaubt')
  return self.raw(url)

def make_job(candidate,source,timestamp,base_by_url):
 url=candidate['url'];title=text(candidate['title'])[:180]
 if not allowed_url(url,source['allowedHosts']) or not SCI.search(title):return None
 places=candidate.get('cities') or cities(unquote(urlparse(url).path)) or ['Standort prüfen']
 context=candidate.get('context','');category='initiative' if re.search(r'initiativ|general opportunity',title,re.I) or re.search(r'keine konkrete vakanz|freuen.{0,100}initiativbewerbung',context,re.I) else 'active'
 old=base_by_url.get(canonical(url));id=old['id'] if old else stable_id(url)
 return {'id':id,'firm':source['firm'],'title':title,'cities':places,'fields':fields(title),'category':category,'evidence':'verified','desc':'Auf einer offiziellen Stellenquelle gefunden. Voraussetzungen, Arbeitstage und Verfügbarkeit für Januar–August 2027 im Original prüfen.','requirements':'In der offiziellen Ausschreibung prüfen.','hours':'Mit dem jeweiligen Team klären.','url':url,'source':source['url'],'checked':timestamp[:10],'auto':True,'sourceId':source['id'],'availability':'seen','firstSeenAt':timestamp,'lastSeenAt':timestamp,'isNew':old is None}

def crawl(source,base_by_url,stamp):
 fetch=Fetcher(source);found=[];seen_pages=set();ads=set();expected=None;truncated=False;complete=False;notes=[]
 try:
  if source.get('adapter')=='personio':
   body,final=fetch.get(source['feed']);candidates=parse_personio(body,source);complete=True
  else:
   queue=[source['url']];candidates=[]
   while queue and len(seen_pages)<8:
    url=queue.pop(0)
    if url in seen_pages:continue
    seen_pages.add(url)
    try:body,final=fetch.get(url)
    except Exception:
     if len(seen_pages)==1:raise
     truncated=True;break
    if re.search(r'captcha|verify you are human|access denied',body[:20000],re.I):raise Blocked('Quelle verlangt eine interaktive Zugriffsprüfung')
    page_jobs,next_pages,ad_keys,total=parse_html(body,final);candidates+=page_jobs;ads.update(ad_keys)
    if total is not None:expected=total if expected is None else max(expected,total)
    for nxt in next_pages:
     if allowed_url(nxt,source['allowedHosts']) and nxt not in seen_pages and nxt not in queue:queue.append(nxt)
   truncated=truncated or bool(queue)
   complete=expected is not None and len(ads)>=expected and not truncated
  unique={canonical(c['url']):c for c in candidates if allowed_url(c['url'],source['allowedHosts'])}
  # Detail pages are only needed for unclear locations or a potentially generic initiative entry.
  for candidate in list(unique.values())[:200]:
   if len(notes)<6 and (not candidate.get('cities') or source.get('checkDetails')):
    try:
     notes.append(candidate['url']);detail,_=fetch.get(candidate['url'])
     if EXPIRED.search(DOM(detail).root.content()[:15000]):continue
     detail_jobs,_,_,_=parse_html(detail,candidate['url'])
     match=next((j for j in detail_jobs if canonical(j['url'])==canonical(candidate['url']) and j.get('cities')),None)
     if match:candidate['cities']=match['cities']
     # Only a narrow main-content introduction may classify a position as initiative.
     main=next((n for n in DOM(detail).root.walk() if n.tag=='main'),None)
     if main:candidate['context']=main.content()[:1200]
    except HTTPError as e:
     if e.code in [404,410]:continue
    except (URLError,TimeoutError,ValueError,Blocked):pass
   job=make_job(candidate,source,stamp,base_by_url)
   if job:found.append(job)
  if not found and not complete:
   state='manual';message='Keine auswertbaren WiMi-Ergebnisse. Suche unvollständig; nicht als fehlende Vakanz gewertet.'
  else:
   state='ok' if complete else 'partial';message='Öffentliche Ergebnisliste verarbeitet.' if complete else 'Teilprüfung: Funde übernommen, vollständige Abdeckung nicht bestätigt.'
  if truncated:message+=' Weitere Ergebnisseiten konnten nicht vollständig verfolgt werden.'
  return found,dict(id=source['id'],firm=source['firm'],url=source['url'],status=state,checkedAt=stamp,complete=complete,found=len(found),message=message)
 except Exception as e:
  message='Automatischer Zugriff nicht möglich; Quelle manuell prüfen.'
  if isinstance(e,Blocked):message=str(e)+'. Manuell prüfen.'
  elif isinstance(e,HTTPError):message='Quelle antwortet mit HTTP '+str(e.code)+'. Manuell prüfen.'
  return [],dict(id=source['id'],firm=source['firm'],url=source['url'],status='blocked' if isinstance(e,Blocked) else 'error',checkedAt=stamp,complete=False,found=0,message=message)

def merge(previous,new_jobs,statuses,stamp):
 prior={canonical(j['url']):dict(j) for j in previous.get('jobs',[])};seen=set();health={s['id']:s for s in statuses}
 for j in new_jobs:
  key=canonical(j['url']);old=prior.get(key);seen.add(key)
  if old:
   j['id']=old['id'];j['firstSeenAt']=old.get('firstSeenAt',stamp);j['isNew']=old.get('isNew',False);j['missingChecks']=0
  prior[key]=j
 for key,j in prior.items():
  if key in seen:continue
  h=health.get(j.get('sourceId'))
  if not h or not h['complete'] or h['status']!='ok':continue
  j['missingChecks']=int(j.get('missingChecks',0))+1
  if j['missingChecks']>=2:j['availability']='not_seen';j['category']='watch'
 # Never infer a job closure from an empty/blocked source. Never touch application state.
 return dict(version=1,researchAt=previous.get('researchAt'),lastRunAt=stamp,jobs=list(prior.values()),sources=statuses)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--sources',default=str(ROOT/'scripts/sources.json'));parser.add_argument('--output',default=str(ROOT/'stellen-updates.json'));parser.add_argument('--only');args=parser.parse_args()
 sources=json.loads(Path(args.sources).read_text());base=json.loads((ROOT/'scripts/base-catalog.json').read_text());base_map={canonical(j['url']):j for j in base};path=Path(args.output);previous=json.loads(path.read_text()) if path.exists() else {};stamp=now()
 if args.only:sources=[s for s in sources if s['id']==args.only]
 results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  futures=[pool.submit(crawl,s,base_map,stamp) for s in sources]
  for f in concurrent.futures.as_completed(futures):results.append(f.result())
 jobs=[j for rows,_ in results for j in rows];statuses=sorted([s for _,s in results],key=lambda x:x['firm'])
 if args.only:
  known={s['id'] for s in statuses};statuses.extend(s for s in previous.get('sources',[]) if s['id'] not in known)
 data=merge(previous,jobs,statuses,stamp);data['jobs'].sort(key=lambda j:(j['firm'],j['title'],j['url']));path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
 print(json.dumps({'sources':len(sources),'found':len(jobs),'complete':sum(s['complete'] for s in statuses),'needsReview':sum(s['status']!='ok' for s in statuses)},ensure_ascii=False))
 if sources and all(s['status'] in ['error','blocked'] for s in statuses):print('::warning::Keine Quelle konnte vollständig verarbeitet werden. Alte Funde bleiben erhalten; Quellenstatus prüfen.')
if __name__=='__main__':main()
