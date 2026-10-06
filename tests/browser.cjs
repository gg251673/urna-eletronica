// Execute contra servidor com DATA_DIR temporário, vazio. Instale Playwright e Chromium.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.TEST_URL||'http://127.0.0.1:5000';
 try {
 await page.goto(base+'/'); await page.getByText('Votação indisponível').waitFor();
 await page.goto(base+'/admin'); await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('wrong');await page.getByRole('button',{name:'Entrar',exact:true}).click();await page.getByText('Usuário ou senha incorretos.').waitFor();
 await page.locator('[name=password]').fill('admin');await page.getByRole('button',{name:'Entrar',exact:true}).click();await page.getByText('Novo candidato',{exact:true}).waitFor();
 await page.locator('[name=name]').fill('Candidata de teste');await page.locator('[name=number]').fill('01');
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
 await page.locator('[name=photo]').setInputFiles({name:'foto.png',mimeType:'image/png',buffer:png});await page.locator('#preview').waitFor({state:'visible'});await page.getByRole('button',{name:'Salvar candidato',exact:true}).click();await page.getByText('Candidato salvo.',{exact:false}).waitFor();
 await page.getByRole('button',{name:'Turnos',exact:true}).click();await page.locator('[name=title]').fill('Teste navegador');await page.locator('[name=office]').fill('Representante');await page.locator('[name=candidates]').check();await page.getByRole('button',{name:'Criar turno',exact:true}).click();await page.getByRole('button',{name:'Abrir turno'}).click();await page.getByText('Turno aberto.',{exact:true}).waitFor();
 const vote=await browser.newPage();await vote.goto(base+'/');await vote.getByRole('heading',{name:'Representante'}).waitFor();
 await vote.keyboard.type('09');await vote.getByText('VOTO NULO',{exact:true}).waitFor();await vote.keyboard.press('Backspace');assert.equal(await vote.locator('.digits').innerText(),'');
 await vote.keyboard.type('01');await vote.getByText('Candidata de teste',{exact:true}).waitFor();await vote.keyboard.press('Enter');await vote.getByText('FIM',{exact:true}).waitFor();await vote.getByText('FIM',{exact:true}).waitFor({state:'hidden'});
 await vote.getByRole('button',{name:'BRANCO',exact:true}).click();await vote.getByRole('button',{name:'CONFIRMA',exact:true}).click();await vote.getByText('FIM',{exact:true}).waitFor();await vote.getByText('FIM',{exact:true}).waitFor({state:'hidden'});
 await vote.keyboard.type('99');await vote.keyboard.press('Enter');await vote.getByText('FIM',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Resultados',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.stats strong')?.textContent==='3');
 await page.getByRole('button',{name:'Turnos',exact:true}).click();page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Encerrar turno'}).click();await page.getByText('Turno encerrado.',{exact:true}).waitFor();
 await vote.reload();await vote.getByText('Votação indisponível').waitFor();
 await vote.setViewportSize({width:390,height:844});await vote.screenshot({path:'/tmp/urna-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('Navegador: login, upload, turno, correção, votos e encerramento passaram.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
