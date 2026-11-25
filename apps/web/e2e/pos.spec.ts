import {expect,test,Page} from "@playwright/test";
import {constants,openSync,writeFileSync,closeSync} from "node:fs";
import path from "node:path";

async function login(page:Page,project:string,scenario:string){
  await page.goto("/login");
  await page.getByLabel("Usuario",{exact:true}).fill(`fictional-${project}-${scenario}`);
  await page.getByLabel("Contraseña",{exact:true}).fill(process.env.MIRA_E2E_PASSWORD!);
  await page.getByRole("button",{name:"Iniciar sesión",exact:true}).click();
  await expect(page).toHaveURL(/\/operations\/catalog$/);
  await page.getByRole("link",{name:"Caja",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Caja de demostración",exact:true})).toBeVisible();
}
async function add(page:Page,sku:string,quantity:string,unit:"EACH"|"KG"){
  const search=page.getByLabel("Código de barras exacto, SKU o comienzo del nombre",{exact:true});
  await search.fill(sku);await search.press("Enter");
  await expect(page.getByRole("button",{name:`Elegir ${sku}`,exact:true})).toBeEnabled();
  await page.getByRole("button",{name:`Elegir ${sku}`,exact:true}).click();
  const form=page.getByRole("form",{name:"Cantidad para carrito"});
  await form.getByLabel("Unidad de transacción",{exact:true}).selectOption(unit);
  await form.getByLabel("Cantidad",{exact:true}).fill(quantity);
  await form.getByLabel("Cantidad",{exact:true}).press("Tab");
  await expect(form.getByLabel("Unidad de transacción",{exact:true})).toBeFocused();
  await form.getByLabel("Unidad de transacción",{exact:true}).press("Shift+Tab");
  await expect(form.getByLabel("Cantidad",{exact:true})).toBeFocused();
  await form.getByRole("button",{name:"Agregar o actualizar línea",exact:true}).click();
}
async function quote(page:Page){
  await page.getByRole("button",{name:"Cotizar carrito",exact:true}).click();
  const q=page.getByRole("region",{name:"Cotización del servidor"});
  await expect(q).toBeVisible();return q;
}
function privateBytes(name:string,buffer:Buffer|string){
  const target=path.join(process.env.MIRA_E2E_ARTIFACT_DIR!,name);
  const fd=openSync(target,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL,0o600);
  try{writeFileSync(fd,buffer);}finally{closeSync(fd);}
}
async function evidence(page:Page,project:string){
  const metrics=[];
  for(const width of [320,390,768,1024,1280,1440]){
    await page.setViewportSize({width,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    const m=await page.evaluate(()=>{
      function rgb(s:string){const n=s.match(/[0-9.]+/g);return n?n.slice(0,3).map(Number):null;}
      function lum(v:number[]){const l=v.map(c=>{const x=c/255;return x<=0.04045?x/12.92:((x+0.055)/1.055)**2.4;});return l[0]*0.2126+l[1]*0.7152+l[2]*0.0722;}
      function contrast(fg:string,bg:string){const a=rgb(fg),b=rgb(bg);if(!a||!b)return null;const x=lum(a),y=lum(b);return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);}
      function background(e:HTMLElement):string {const c=getComputedStyle(e).backgroundColor;if(c!=="rgba(0, 0, 0, 0)"&&c!=="transparent")return c;return e.parentElement?background(e.parentElement):"rgb(255, 255, 255)";}
      return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
      controls:[...document.querySelectorAll<HTMLElement>('main button,main input,main select')].filter(e=>e.getBoundingClientRect().width>0).map((e,i)=>{
        const r=e.getBoundingClientRect(),s=getComputedStyle(e),bg=background(e);
        const target=e instanceof HTMLInputElement&&e.type==="checkbox"?e.closest("label")??e:e;
        const t=target.getBoundingClientRect();
        return {id:e.id||`${e.tagName.toLowerCase()}-${i}`,width:r.width,height:r.height,targetWidth:t.width,targetHeight:t.height,
          disabled:e.matches(":disabled"),color:s.color,background:bg,border:s.borderTopColor,textContrast:contrast(s.color,bg),boundaryContrast:contrast(s.borderTopColor,bg)};})};
    });
    metrics.push(m);
    for(const control of m.controls){
      expect(control.targetHeight).toBeGreaterThanOrEqual(44);expect(control.targetWidth).toBeGreaterThanOrEqual(44);
      if(!control.disabled){expect(control.textContrast).not.toBeNull();expect(control.textContrast!).toBeGreaterThanOrEqual(4.5);}
    }
    if([320,390,1280].includes(width))privateBytes(`pos-${project}-${width}-quote.png`,await page.screenshot({fullPage:true}));
  }
  await page.getByLabel("Código de barras exacto, SKU o comienzo del nombre",{exact:true}).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button",{name:"Buscar",exact:true})).toBeFocused();
  const focus=await page.getByRole("button",{name:"Buscar",exact:true}).evaluate(e=>{const s=getComputedStyle(e);return {selector:"main button Buscar",outlineStyle:s.outlineStyle,outlineWidth:s.outlineWidth,outlineColor:s.outlineColor,outlineOffset:s.outlineOffset};});
  expect(focus.outlineStyle).toBe("solid");expect(parseFloat(focus.outlineWidth)).toBeGreaterThanOrEqual(2);
  privateBytes(`pos-${project}-structural-metrics.json`,JSON.stringify({method:"Native Chromium viewport; not actual Chrome zoom",content:"Screenshots contain fictional draft/quote values; metrics contain structural IDs and colors only",metrics,focus}));
}

test("pos-journey",async({page},info)=>{
  const project=info.project.name;
  await login(page,project,"pos-journey");
  await add(page,`POS-${project}-EACH`,"2","EACH");
  await add(page,`POS-${project}-MASS`,"0.245001","KG");
  let q=await quote(page);await expect(q.getByText("Total ARS 224.50",{exact:true})).toBeVisible();
  await expect(q.getByRole("button",{name:"Confirmar consumo sin liquidar",exact:true})).toBeDisabled();
  await evidence(page,project);
  await q.getByRole("checkbox",{name:"Revisé esta cotización y sus precios",exact:true}).check();
  let posts=0;
  await page.route("**/api/v1/operator/pos/sales",async route=>{
    if(route.request().method()!=="POST"){await route.continue();return;}
    posts++;await route.fulfill({status:409,json:{error:{code:"pos_conflict"}}});
  });
  try{
    await q.getByRole("button",{name:"Confirmar consumo sin liquidar",exact:true}).click();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("Conservamos el carrito");
    await expect(page.getByRole("region",{name:"Cotización del servidor"})).toHaveCount(0);
    expect(posts).toBe(1);
  }finally{await page.unroute("**/api/v1/operator/pos/sales");}
  q=await quote(page);await expect(q.getByRole("checkbox")).not.toBeChecked();
  await q.getByRole("checkbox").check();
  await q.getByRole("button",{name:"Confirmar consumo sin liquidar",exact:true}).click();
  const sale=page.getByRole("region",{name:"Operación registrada"});
  await expect(sale.getByRole("heading",{name:"Consumo registrado · Sin liquidar",exact:true})).toBeVisible();
  await expect(sale.getByText("ARS 224.50",{exact:true})).toBeVisible();
  await expect(sale).toContainText("No es comprobante fiscal ni acredita pago");
  await page.getByRole("button",{name:"Consultar historial",exact:true}).click();
  await expect(page.getByRole("region",{name:"Historial POS"}).getByRole("button",{name:/Ver consumo/}).first()).toBeVisible();
});

test("pos-lost",async({page},info)=>{
  const project=info.project.name;
  await login(page,project,"pos-lost");
  await expect(page.getByRole("region",{name:"Historial POS"})).toHaveCount(0);
  expect((await page.request.get("/api/v1/operator/pos/sales")).status()).toBe(403);
  await add(page,`POS-${project}-EACH`,"1","EACH");
  const q=await quote(page);await q.getByRole("checkbox").check();
  let posts=0;
  await page.route("**/api/v1/operator/pos/sales",async route=>{
    if(route.request().method()!=="POST"){await route.continue();return;}
    posts++;const response=await route.fetch();
    try{
      if(response.status()!==200){const payload=await response.json();console.log("MIRA_POS_HTTP="+JSON.stringify({status:response.status(),code:payload?.error?.code,content_type:response.headers()["content-type"]}));}
      expect(response.status()).toBe(200);
    }finally{await route.abort("failed");}
  });
  try{
    await q.getByRole("button",{name:"Confirmar consumo sin liquidar",exact:true}).click();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("Resultado incierto");
    await expect(page.getByRole("region",{name:"Operación pendiente"})).toBeVisible();
    await expect(page.getByRole("button",{name:"Confirmar consumo sin liquidar",exact:true})).toBeDisabled();
    expect(posts).toBe(1);
  }finally{await page.unroute("**/api/v1/operator/pos/sales");}
  await page.reload();
  await expect(page.getByRole("region",{name:"Operación pendiente"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Cotizar carrito",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Consultar operación",exact:true}).click();
  await expect(page.getByRole("region",{name:"Operación registrada"}).getByText("ARS 100.00",{exact:true})).toBeVisible();
  await expect(page.getByRole("region",{name:"Operación pendiente"})).toHaveCount(0);
  expect(posts).toBe(1);
});
