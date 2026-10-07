import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';

export async function designWorkflows({page, ui=page, base, label, output, saveDownload=(download,path)=>download.saveAs(path)}) {
  const cases=JSON.parse(await readFile(fileURLToPath(new URL('../../../tests/fixtures/workflow_cases.json',import.meta.url)),'utf8'));
  await mkdir(output,{recursive:true});
  const logs=[];
  async function settled(){await ui.waitForFunction(()=>!document.querySelector('#viewer-mode').disabled && !document.querySelector('#download').disabled);}
  async function post(button, endpoint){
    const pending=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(endpoint)&&r.request().method()==='POST');
    await ui.locator(button).click();
    const response=await pending;
    assert.equal(response.status(),200,await response.text());
    await settled();
    return response.json();
  }
  async function select(query){
    await ui.locator('#selection-syntax').selectOption('expression');
    await ui.locator('#viewer-command').fill(query);
    const response=page.waitForResponse(r=>r.url().endsWith('/selections/query'));
    await ui.locator('#run-viewer-command').click();
    const selected=await (await response).json();
    assert.ok(selected.count>0,query);
    await ui.waitForFunction(count=>document.querySelector('#selection-count').textContent.startsWith(`${count} atom`),selected.count);
    return selected;
  }
  for(const scenario of cases){
    const loaded=page.waitForResponse(r=>r.url().endsWith('/sessions')&&r.request().method()==='POST');
    await ui.locator('#file-input').setInputFiles(fileURLToPath(new URL('../../../examples/'+scenario.file,import.meta.url)));
    const session=await (await loaded).json();
    assert.equal(session.structure.atom_count,scenario.atoms);
    await settled();
    const endpoint=`${base}api/v1/sessions/${session.session_id}`;
    async function snapshot(stage){
      const response=await page.request.get(endpoint+'/cif');
      assert.equal(response.status(),200);
      await writeFile(join(output,`${label}-${scenario.name}-${stage}.cif`),await response.body());
    }
    await snapshot('00');
    for(const [i,action] of scenario.actions.entries()){
      let result;
      if(['condition','group','pair'].includes(action.kind)){
        await ui.locator('#editor-tab-annotations').click();
        await ui.locator('#condition').selectOption(action.key??'distance');
        const selected=await select(action.query);
        if(action.kind==='condition'){
          await ui.locator('#enabled').setChecked(action.enabled);
          if(action.value!==undefined) await ui.locator('#condition-value-input').selectOption(String(action.value));
          result=await post('#apply-condition','/conditions');
        }else if(action.kind==='group')result=await post('#group-new','/groups');
        else{
          assert.equal(selected.count,2);
          await ui.locator('#pair-use-selection').click();
          await ui.locator('#pair-distance').fill(String(action.value));
          await ui.locator('#pair-add').click();
          result=await post('#pair-apply','/distance-restraints/replace');
        }
      }else if(action.kind==='charge'){
        await ui.locator('#editor-tab-charges').click();
        await select(action.query);
        await ui.locator('#charge').fill(String(action.value));
        result=await post('#apply-charge','/charges');
      }else if(action.kind==='undo')result=await post('#undo','/undo');
      else if(action.kind==='realize'){
        await ui.locator('#editor-tab-main').click();
        const heading=ui.getByText('Expandable segments',{exact:true});
        if(!await heading.evaluate(el=>el.parentElement.open))await heading.click();
        result=await post('#realize-segments','/segments/realize');
        assert.ok(result.structure.atom_count>scenario.atoms);
      }
      await snapshot(String(i+1).padStart(2,'0'));
      logs.push({scenario:scenario.name,step:i+1,action:action.kind,atoms:result.structure.atom_count});
    }
    await ui.locator('#editor-tab-annotations').click();
    await ui.locator('#condition').selectOption(scenario.name==='ppi'?'hotspot':'rasa');
    const download=page.waitForEvent('download');
    const checked=page.waitForResponse(r=>r.url().endsWith('/export'));
    await ui.locator('#download').click();
    if((await (await checked).json()).issues.length)await ui.locator('dialog.confirmation button[value="confirm"]').click();
    await saveDownload(await download,join(output,`${label}-${scenario.name}-saved.cif`));
    await page.screenshot({path:join(output,`${label}-${scenario.name}.png`)});
    console.log(`${label} (${await ui.locator('#viewer').getAttribute('data-backend')}): ${scenario.name} edits, restraint/group, segment expansion, Undo and Save passed`);
  }
  await writeFile(join(output,`${label}-actions.json`),JSON.stringify(logs,null,2));
}
