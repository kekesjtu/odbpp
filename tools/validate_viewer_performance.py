"""Compare standalone viewer interactions and rendering work in real Chromium.

uv run --all-extras --with playwright python tools/validate_viewer_performance.py
The browser dependency is validation-only; exported viewers remain standalone.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import statistics
import subprocess

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CASES = ["minimal-test-odb", "kicad-kitchen-sink", "kicad-video-odb.tgz",
         "designodb-rigidflex.tgz"]


def constants(source: str) -> dict[str, str]:
    return {node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"_CSS", "_JS"}}


def viewer(text: str, values: dict[str, str]) -> str:
    start = text.index("<style>") + len("<style>")
    end = text.index("</style>", start)
    text = text[:start] + values["_CSS"] + text[end:]
    start = text.index("<script>") + len("<script>")
    end = text.index("</script>", start)
    return text[:start] + values["_JS"] + text[end:]


WAIT = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
SNAPSHOT = """() => {
  const root=document.getElementById('odb-root'), m=root.transform.baseVal.consolidate().matrix;
  const attrs=(nodes,keys)=>Array.from(nodes,n=>keys.map(k=>n.getAttribute(k)));
  return {
    transform:[m.a,m.b,m.c,m.d,m.e,m.f],
    position:document.getElementById('pos').textContent,
    measure:document.getElementById('measure-value').textContent,
    lines:attrs(document.querySelectorAll('#odb-measure line'),['x1','y1','x2','y2']),
    rows:Array.from(document.querySelectorAll('.row'),r=>[r.dataset.name,r.hidden,
      r.classList.contains('sel'),r.querySelector('input').checked]),
    layers:attrs(document.querySelectorAll('.odb-layer'),['data-name','style']),
    domains:attrs(document.querySelectorAll('#odb-domains path'),['data-name','style']),
    profile:document.getElementById('odb-profile')?.style.display,
    info:[document.getElementById('info').style.display,document.getElementById('info').textContent],
    infoScroll:document.getElementById('info').scrollTop,
    matrix:attrs(document.querySelectorAll('.matrix-row'),['data-name','aria-selected','class']),
    spans:attrs(document.querySelectorAll('.drill-span'),['data-name','aria-pressed']),
    arrows:attrs(document.querySelectorAll('.drill-span path'),['d']),
    matrixText:document.getElementById('matrix-selection').textContent,
    buttons:Array.from(document.querySelectorAll('#tools button'),b=>[b.id,b.className]),
    wire:document.body.className,
    search:document.getElementById('search-count').textContent
  };
}"""
INSTRUMENT = """() => {
  window.work={ctm:0,bounds:0,transform:0,position:0};
  for(const [proto,name,key] of [[SVGGraphicsElement.prototype,'getScreenCTM','ctm'],
     [Element.prototype,'getBoundingClientRect','bounds'],[Element.prototype,'setAttribute','transform']]){
    const original=proto[name];proto[name]=function(...args){
      if(key!=='transform'||(this.id==='odb-root'&&args[0]==='transform'))window.work[key]++;
      return original.apply(this,args);
    };
  }
  new MutationObserver(ms=>{window.work.position+=ms.length}).observe(
    document.getElementById('pos'),{childList:true});
}"""
BURST = """({kind,count}) => {
  const svg=document.querySelector('.odb-svg'),b=svg.getBoundingClientRect();
  const x=b.left+b.width*.42,y=b.top+b.height*.47;
  if(kind==='drag')svg.dispatchEvent(new MouseEvent('mousedown',{button:0,clientX:x,clientY:y,bubbles:true}));
  const start=performance.now();
  for(let i=0;i<count;i++){
    if(kind==='wheel')svg.dispatchEvent(new WheelEvent('wheel',{clientX:x+i%7,clientY:y+i%5,
      deltaY:i%2?1:-1,cancelable:true,bubbles:true}));
    else svg.dispatchEvent(new MouseEvent('mousemove',{clientX:x+i%31,clientY:y+i%23,bubbles:true}));
  }
  if(kind==='drag')window.dispatchEvent(new MouseEvent('mouseup'));
  return performance.now()-start;
}"""


def exercise(page):
    snapshots = []
    def capture(label):
        page.evaluate(WAIT)
        snapshots.append((label, page.evaluate(SNAPSHOT)))
    def click(id):
        page.locator('#'+id).click()
        capture(id)
    capture('initial')
    page.evaluate(INSTRUMENT)
    for kind in ('hover', 'drag', 'wheel'):
        page.evaluate(BURST, dict(kind=kind, count=120))
        capture(kind)
    page.evaluate("""() => {
      const s=document.querySelector('.odb-svg'),b=s.getBoundingClientRect();
      s.dispatchEvent(new MouseEvent('mousedown',{button:0,clientX:b.left+80,clientY:b.top+90,bubbles:true}));
      for(const [x,y] of [[10,20],[45,60]])document.body.dispatchEvent(new MouseEvent('mousemove',{clientX:x,clientY:y,bubbles:true}));
      window.dispatchEvent(new MouseEvent('mouseup'));
    }""")
    capture('drag-outside')
    # Browser-generated events exercise SVG hit testing as well as listeners.
    box=page.locator('.odb-svg').bounding_box()
    x,y=box['x']+box['width']*.5,box['y']+box['height']*.5
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+29,y+21,steps=6);page.mouse.up()
    capture('pointer-drag')
    click('zin'); click('zout'); click('fit')
    for id in ('none','all','copper','invert'):
        click(id)
    page.locator('.row input').first.click()
    capture('checkbox')
    page.locator('.row').nth(1).click()
    capture('selection')
    click('isolate'); click('fit-layer'); click('domainbtn'); click('profilebtn')
    page.locator('#info').evaluate('(el)=>el.scrollTop=80')
    click('propertiesbtn'); click('propertiesbtn')
    page.locator('.row').nth(1).dblclick()
    capture('double-isolate')
    page.locator('#layer-search').fill('signal')
    capture('search')
    page.locator('#layer-search').fill('')
    page.locator('#sort-order').select_option('stackup')
    capture('stackup')
    page.locator('#sort-order').select_option('type')
    click('fit'); click('measurebtn')
    page.evaluate("""() => {const s=document.querySelector('.odb-svg'),b=s.getBoundingClientRect();
      for(const [x,y] of [[.3,.4],[.6,.7]])s.dispatchEvent(new MouseEvent('mousedown',
        {button:0,clientX:b.left+b.width*x,clientY:b.top+b.height*y,bubbles:true}));}""")
    capture('measure')
    page.locator('body').press('Escape')
    capture('escape')
    click('measurebtn'); click('propertiesbtn'); click('wirebtn')
    page.locator('#overview svg').click(position=dict(x=91,y=48))
    capture('overview')
    click('matrixbtn')
    if page.locator('.drill-span').count():
        page.locator('.drill-span').first.press('Enter')
        capture('span')
    page.set_viewport_size(dict(width=980,height=820))
    capture('resize-matrix')
    page.locator('#matrix').evaluate('(el)=>el.scrollTop=50')
    capture('scroll-matrix')
    click('matrix-close')
    page.set_viewport_size(dict(width=620,height=860))
    capture('mobile')
    page.locator('body').press('f')
    capture('mobile-fit')
    page.locator('body').press('w')
    page.locator('body').press('a')
    capture('keyboard')
    image = page.screenshot()
    # Also verify zoom limits and focal-point anchoring independently of the
    # baseline's floating-point accumulation at very large magnifications.
    limits = page.evaluate("""async () => {
      const s=document.querySelector('.odb-svg'),root=document.getElementById('odb-root');
      document.getElementById('fit').click();
      await new Promise(requestAnimationFrame);
      const b=s.getBoundingClientRect(),x=Math.floor(b.left+b.width*.35),y=Math.floor(b.top+b.height*.65);
      const point=s.createSVGPoint();point.x=x;point.y=y;
      const anchor=point.matrixTransform(root.getScreenCTM().inverse());
      const results=[];
      for(const [delta,expected] of [[-1,5000],[1,.02]]){
        for(let i=0;i<120;i++)s.dispatchEvent(new WheelEvent('wheel',{
          clientX:x,clientY:y,deltaY:delta,cancelable:true,bubbles:true}));
        await new Promise(requestAnimationFrame);
        const m=root.getScreenCTM(),now=anchor.matrixTransform(m);
        results.push({scale:root.transform.baseVal.consolidate().matrix.a,
          expected,error_px:Math.hypot(now.x-x,now.y-y)});
      }
      return results;
    }""")
    for result in limits:
        assert abs(result['scale']-result['expected'])<1e-6, result
        assert result['error_px']<1, result
    return snapshots, image, limits


def compare(before, after):
    max_error = 0.0
    assert len(before) == len(after)
    for (label, a), (other, b) in zip(before, after):
        assert label == other
        for key in a:
            if key == 'transform':
                error = max(abs(x-y) for x,y in zip(a[key],b[key]))
                max_error=max(error,max_error)
                assert error < 0.002, (label,key,a[key],b[key])
            elif key == 'lines':
                assert len(a[key]) == len(b[key])
                for x,y in zip(a[key],b[key]):
                    assert all(abs(float(u)-float(v))<0.0001 for u,v in zip(x,y)), (label,key,x,y)
            else:
                assert a[key] == b[key], (label,key,a[key],b[key])
    return max_error


def metrics(session):
    return {m['name']:m['value'] for m in session.send('Performance.getMetrics')['metrics']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref',default='dbc45e7')
    parser.add_argument('--repeat',type=int,default=5)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/reviews/viewer-performance-validation.json')
    parser.add_argument('--case',action='append',choices=CASES)
    args=parser.parse_args()
    old=constants(subprocess.check_output(['git','show',f'{args.baseline_ref}:src/odbpp/svg.py'],cwd=ROOT,text=True,encoding='utf-8'))
    new=constants((ROOT/'src/odbpp/svg.py').read_text(encoding='utf-8'))
    scratch=ROOT/'build/viewer-validation';scratch.mkdir(parents=True,exist_ok=True)
    report=dict(baseline_ref=args.baseline_ref,browser='Chrome headless',viewport=[1280,800],
                repetitions=args.repeat,cases=[],checks='real DOM interactions, geometry hash, final screenshot')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        report['browser_version']=browser.version
        for case in args.case or CASES:
            source=next(path for path in (ROOT/'out'/case).glob('*.html') if not path.name.endswith('.images.html'))
            original=source.read_text(encoding='utf-8')
            paths=[]
            for label,values in [('before',old),('after',new)]:
                path=scratch/f'{case}-{label}.html';path.write_text(viewer(original,values),encoding='utf-8');paths.append(path)
            results=[];timings={};work={};durations={}
            for label,path in zip(('before','after'),paths):
                page=browser.new_page(viewport=dict(width=1280,height=800))
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(path.as_uri());page.evaluate(WAIT)
                results.append(exercise(page))
                assert not errors,errors
                page.close()
                samples={kind:[] for kind in ('hover','drag','wheel')};counts={};totals={}
                for kind in samples:
                    rows=[];jobs=[]
                    for _ in range(args.repeat):
                        page=browser.new_page(viewport=dict(width=1280,height=800));page.goto(path.as_uri());page.evaluate(WAIT)
                        page.evaluate(INSTRUMENT)
                        session=page.context.new_cdp_session(page);session.send('Performance.enable')
                        a=metrics(session)
                        samples[kind].append(page.evaluate(BURST,dict(kind=kind,count=400)))
                        page.evaluate(WAIT);b=metrics(session)
                        rows.append(page.evaluate('window.work'))
                        jobs.append({k:b[k]-a[k] for k in ('LayoutCount','RecalcStyleCount','LayoutDuration','RecalcStyleDuration','ScriptDuration')})
                        page.close()
                    counts[kind]={k:statistics.median(row[k] for row in rows) for k in rows[0]}
                    totals[kind]={k:statistics.median(row[k] for row in jobs) for k in jobs[0]}
                timings[label]={k:statistics.median(v) for k,v in samples.items()}
                work[label]=counts;durations[label]=totals
            error=compare(results[0][0],results[1][0])
            a=Image.open(io.BytesIO(results[0][1])).convert('RGB');b=Image.open(io.BytesIO(results[1][1])).convert('RGB')
            diff=ImageChops.difference(a,b)
            assert diff.getbbox() is None, (case,'final screenshot changed',diff.getbbox())
            entry=dict(case=case,checkpoints=len(results[0][0]),max_transform_difference_mm=error,
                       screenshot_identical=True,burst_events=400,event_handler_ms=timings,work=work,cdp=durations,
                       zoom_limits={label:result[2] for label,result in zip(('before','after'),results)},
                       svg_sha256=hashlib.sha256(original[original.rindex('<svg',0,original.index('class="odb-svg"')):
                           original.index('</svg>',original.index('class="odb-svg"'))+6].encode()).hexdigest())
            report['cases'].append(entry)
            print(json.dumps(entry,ensure_ascii=False),flush=True)
        browser.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(args.output)


if __name__=='__main__':
    main()
