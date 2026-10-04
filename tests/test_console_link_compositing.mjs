// Synthetic controls for the exact browser SAMPLE function; not browser evidence.
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import vm from 'node:vm'

const source=await readFile(new URL('./console_link_contrast.py',import.meta.url),'utf8')
const code=source.match(/SAMPLE = r"""([\s\S]*?)"""/)[1]
const defaults={backgroundColor:'rgba(0, 0, 0, 0)',backgroundImage:'none',filter:'none',mixBlendMode:'normal',opacity:'1',
  color:'rgb(147, 197, 253)',fontSize:'12px',fontWeight:'400',textDecorationLine:'underline'}
function sample(styles){
  const nodes=styles.map(style=>({style:{...defaults,...style}}))
  for(let i=0;i<nodes.length;i++)nodes[i].parentElement=nodes[i+1]||null
  const anchor=Object.assign(nodes[0],{textContent:'Synthetic link',tabIndex:0,tagName:'A',
    getAttribute:key=>key==='href'?'/synthetic':null,hasAttribute:()=>false,closest:()=>null,
    matches:()=>false,getBoundingClientRect:()=>({x:0,y:0,width:90,height:20})})
  let visited=false
  const context={getComputedStyle:e=>e.style,NodeFilter:{SHOW_TEXT:4},document:{
    createTreeWalker:()=>({nextNode:()=>visited?null:(visited=true,{textContent:'Synthetic link',parentElement:anchor})}),
    body:{getAttribute:()=> 'dark'}}}
  return vm.runInNewContext('('+code+')',context)(anchor)
}
let result=sample([{}, {backgroundColor:'rgb(35, 35, 36)'}])
assert.deepEqual([...result.effective_background],[35,35,36])
assert.deepEqual([...result.foreground],[147,197,253])
assert.equal(result.font_size,'12px')
result=sample([{color:'rgba(100, 200, 240, 0.5)'},{backgroundColor:'rgb(20, 40, 60)'}])
assert.deepEqual([...result.foreground],[60,120,150])
result=sample([{}, {backgroundColor:'rgba(100, 100, 100, 0.5)'}, {backgroundColor:'rgb(20, 40, 60)'}])
assert.deepEqual([...result.effective_background],[60,70,80])
for(const style of [{opacity:'0.5'},{filter:'blur(2px)'},{backgroundImage:'linear-gradient(red,blue)'},{mixBlendMode:'multiply'}]){
  assert.throws(()=>sample([style]),/unsupported compositing/)
}
assert.throws(()=>sample([{color:'color(display-p3 1 0 0)'}]),/unsupported computed color/)
console.log('PASS: exact SAMPLE opaque/alpha/ancestor compositing and 5 unsupported-style negative controls; browser=false')
