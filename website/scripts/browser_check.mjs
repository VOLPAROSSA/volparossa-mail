// Optional development-only QA. No runtime or model is launched.
import {chromium, firefox, webkit} from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(root,process.env.QA_OUTPUT||'qa/browser');fs.mkdirSync(out,{recursive:true});
const url=process.env.SITE_QA_URL||'http://127.0.0.1:8781/volparossa-mail/';
const assert=(v,m)=>{if(!v)throw Error(m);};
const results=[],accessibility=[];
const sections=['hero','foundation','delivery','offline-custody','own-mailbox','shared-brain','privacy','evidence','contribute'];

async function loadAll(page){
 await page.evaluate(()=>document.fonts.ready);
 for(const section of await page.locator('main>section').all()){
  await section.scrollIntoViewIfNeeded();
  for(const image of await section.locator('img').all()){
   if(!await image.isVisible())continue;
   await image.scrollIntoViewIfNeeded();
   await page.waitForFunction(i=>i.complete&&i.naturalWidth>0,await image.elementHandle());
   await image.evaluate(i=>i.decode());
  }
 }
 await page.evaluate(()=>scrollTo(0,0));
}
async function geometry(page){
 return page.evaluate(()=>({
  overflow:document.documentElement.scrollWidth>innerWidth,
  headerOverlap:document.querySelector('.site-header').getBoundingClientRect().bottom>document.querySelector('.mail-breadcrumb').getBoundingClientRect().top+1,
  missingImages:[...document.images].filter(i=>i.getClientRects().length&&(!i.complete||!i.naturalWidth)).map(i=>i.currentSrc),
  interfaceFits:(()=>{const e=document.querySelector('.mail-client');return e.scrollWidth<=e.clientWidth+1;})(),
  meshPlaneAligned:(()=>{const s=document.querySelector('.mail-mesh-stage').getBoundingClientRect(),i=document.querySelector('.mail-mesh-stage img').getBoundingClientRect();return Math.abs(s.width-i.width)<1&&Math.abs(s.height-i.height)<1;})(),
  upstreamClear:(()=>{const m=document.querySelector('.mail-upstream').getBoundingClientRect(),u=document.querySelector('.mail-interface').getBoundingClientRect();return m.right<=u.left+1||m.bottom<=u.top+1;})(),
  ordinaryLabelsClear:(()=>{const es=[...document.querySelectorAll('.mail-ordinary-route li span')].map(e=>e.getBoundingClientRect());return es.every((e,i)=>i===0||e.left>=es[i-1].right-1);})(),
  meshNodes:[...document.querySelectorAll('.mail-mesh')].filter(e=>e.getBoundingClientRect().width>0).map(e=>({peers:e.querySelectorAll('[data-peer]').length,edges:e.querySelectorAll('[data-edge]').length,endpoints:e.querySelectorAll('[data-endpoint]').length})),
  width:innerWidth,height:innerHeight
 }));
}
// Add webkit via QA_ENGINES when its platform dependencies are available.
for(const engine of (process.env.QA_ENGINES||'chromium,firefox').split(',')){
 const browser=await (engine==='chromium'?chromium.launch({executablePath:process.env.CHROME_PATH||undefined,args:['--no-sandbox']}):engine==='firefox'?firefox.launch():webkit.launch());
 try{
  const sizes=engine==='chromium'?[[1440,900],[390,844],[320,740],[768,1024],[1920,1080],[844,390]]:engine==='firefox'?[[390,844],[1440,900],[2560,1440]]:[[390,844],[1440,900]];
  for(const [width,height] of sizes){
   const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'}),page=await context.newPage();
   const errors=[],failures=[],external=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('requestfailed',r=>failures.push(r.url()+' '+r.failure()?.errorText));
   page.on('response',r=>{if(r.status()>=400)failures.push(r.status()+' '+r.url());});
   page.on('request',r=>{if(!r.url().startsWith(new URL(url).origin))external.push(r.url());});
   await page.goto(url,{waitUntil:'networkidle'});
   await page.evaluate(()=>document.fonts.ready);
   await page.locator('.mail-hero img').evaluate(i=>i.decode());
   const initialTransfer=await page.evaluate(()=>performance.getEntriesByType('resource').reduce((n,r)=>n+r.transferSize,0)+performance.getEntriesByType('navigation')[0].transferSize);
   await loadAll(page);
   const g=await geometry(page);
   assert(g.meshNodes.length===1&&g.meshNodes[0].peers===6&&g.meshNodes[0].edges===11&&g.meshNodes[0].endpoints===2,'Complete visible mesh');
   assert(!g.overflow&&!g.headerOverlap&&!g.missingImages.length,JSON.stringify({engine,...g}));
   assert(g.interfaceFits&&g.meshPlaneAligned&&g.upstreamClear&&g.ordinaryLabelsClear,'Interface and network fit: '+JSON.stringify(g));
   assert(!errors.length&&!failures.length&&!external.length,JSON.stringify({errors,failures,external}));
   assert(await page.locator('.site-header [aria-current]').count()===2 && await page.locator('.site-header [aria-current]').evaluateAll(es=>es.every(e=>e.href==='https://volparossa.org/applications/')),'Incorrect shared navigation active state');
   assert(await page.locator('.mail-breadcrumb [aria-current=page]').innerText()==='Mail','Breadcrumb state');
   const transfer=await page.evaluate(()=>({total:performance.getEntriesByType('resource').reduce((n,r)=>n+r.transferSize,0)+performance.getEntriesByType('navigation')[0].transferSize,images:performance.getEntriesByType('resource').filter(r=>r.initiatorType==='img').reduce((n,r)=>n+r.transferSize,0),fonts:performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.woff2')).reduce((n,r)=>n+r.transferSize,0)}));
   if(width===390)assert(initialTransfer<=1200000,'Mobile initial transfer budget');
   assert(transfer.images<=(width<=900?3000000:5000000),'Total image transfer budget');
   if(engine==='chromium'&&[390,1440].includes(width)){
    const a=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
    accessibility.push({width,violations:a.violations});
    fs.writeFileSync(path.join(out,'accessibility.json'),JSON.stringify(accessibility,null,2));
    assert(!a.violations.length,'Axe violations: '+a.violations.map(v=>v.id).join(','));
   }
   await page.screenshot({path:path.join(out,`${engine}-${width}-full.png`),fullPage:true});
   if([390,1440].includes(width))for(const id of sections)await page.locator('#'+id).screenshot({path:path.join(out,`${engine}-${width}-${id}.png`)});
   // Additional explanations remain usable with the keyboard, without page scripts.
   if([390,1440].includes(width)){
    for(const disclosure of await page.locator('main details').all()){
     await disclosure.locator('summary').focus();await page.keyboard.press('Enter');
     assert(await disclosure.getAttribute('open')!==null,'Explanation opens with Enter');
     assert(await disclosure.locator('p').first().isVisible(),'Full explanation is visible');
    }
    const expanded=await geometry(page);
    assert(!expanded.overflow&&!expanded.headerOverlap,'Expanded explanations reflow');
    if(engine==='chromium'){
     const a=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
     accessibility.push({width,expanded:true,violations:a.violations});
     assert(!a.violations.length,'Expanded explanation axe violations');
    }
    await page.screenshot({path:path.join(out,`${engine}-${width}-expanded.png`),fullPage:true});
   }
   // Keyboard access, native disclosure and shared menu enhancement.
   await page.goto(url,{waitUntil:'networkidle'});await page.keyboard.press('Tab');
   assert(await page.locator('.skip-link').evaluate(e=>e===document.activeElement),'Skip link first');
   assert(await page.locator('.skip-link').evaluate(e=>getComputedStyle(e).clipPath==='none'),'Skip link visible');
   await page.keyboard.press('Enter');assert(await page.locator('#main').evaluate(e=>e===document.activeElement),'Main focus');
   if(width<=800){
    const menu=page.locator('.mobile-nav');await menu.locator('summary').click();assert(await menu.getAttribute('open')!==null,'Native menu');
    await page.keyboard.press('Escape');assert(await menu.getAttribute('open')===null,'Menu Escape');
    await menu.locator('summary').click();await page.locator('#foundation h2').click();assert(await menu.getAttribute('open')===null,'Menu outside click');
   }
   results.push({engine,...g,initialTransfer,...transfer,errors,failures,external});
   await context.close();
  }
  if(engine==='chromium')for(const type of ['no-js','text-200-percent','zoom-400-percent','images-disabled']){
   const width=type==='text-200-percent'?1440:type==='zoom-400-percent'?320:390;
   const context=await browser.newContext({viewport:{width,height:type==='zoom-400-percent'?225:900},javaScriptEnabled:type!=='no-js',reducedMotion:'reduce'});
   const page=await context.newPage();
   if(type==='text-200-percent')await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>document.documentElement.style.fontSize='200%'));
   if(type==='images-disabled')await page.route(/\.(avif|webp|jpe?g|png)$/,route=>route.abort());
   await page.goto(url,{waitUntil:'networkidle'});
   if(type!=='images-disabled')await loadAll(page);
   const g=await geometry(page);assert(!g.overflow&&!g.headerOverlap,JSON.stringify({type,...g}));
   assert(g.interfaceFits&&g.meshPlaneAligned&&g.upstreamClear&&g.ordinaryLabelsClear,JSON.stringify({type,...g}));
   if(type==='no-js'){const m=page.locator('.mobile-nav');await m.locator('summary').click();assert(await m.getAttribute('open')!==null,'No-JS menu');await m.locator('summary').click();}
   if(type==='no-js'){
    for(const d of await page.locator('main details').all()){
     await d.locator('summary').click();assert(await d.locator('p').first().isVisible(),'Explanation works without JavaScript');
    }
   }
   assert(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior)==='auto','Reduced-motion scrolling');
   await page.screenshot({path:path.join(out,type+'.png'),fullPage:true});
   results.push({type,...g,passed:true});await context.close();
  }
 }finally{await browser.close();}
}
fs.writeFileSync(path.join(out,'accessibility.json'),JSON.stringify(accessibility,null,2)+'\n');
fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checkedAt:new Date().toISOString(),url,results,accessibility},null,2)+'\n');
console.log(JSON.stringify({profiles:results.length,accessibility:accessibility.map(a=>({width:a.width,violations:a.violations.length})),results}));
