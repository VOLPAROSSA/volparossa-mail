// Maintainer-only typography render, independent of AI-generated scenery.
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const assets=process.env.SITE_QA_URL||'http://127.0.0.1:8781/volparossa-mail/';
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||undefined,args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1200,height:630},deviceScaleFactor:1});
 await page.setContent(fs.readFileSync(path.join(root,'masters/social.html'),'utf8').replaceAll('{{assetroot}}',assets),{waitUntil:'networkidle'});
 await page.evaluate(()=>document.fonts.ready);
 await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode())));
 await page.screenshot({path:path.join(root,'assets/mail/social.jpg'),type:'jpeg',quality:88});
}finally{await browser.close();}
