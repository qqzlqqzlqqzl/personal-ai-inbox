import assert from 'node:assert/strict'
import { draftKey, MAX_NOTE, readDraft, storeDraft, removeDraft } from '../frontend-review/after/src/components/Ai/note-drafts.js'
import { navigationCommands, matchCommands } from '../frontend-review/after/src/components/Ai/navigation-commands.js'
import { safePublicImage } from '../frontend-review/after/src/components/Article/safe-image-url.js'
const values = new Map(), storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}
const key=draftKey('server-a',1,42)
assert.notEqual(key,draftKey('server-b',1,42));assert.notEqual(key,draftKey('server-a',2,42));assert.notEqual(key,draftKey('server-a',1,43))
assert.equal(storeDraft(storage,key,'draft','server'),true);assert.equal(readDraft(storage,key).base,'server')
removeDraft(storage,key,'older');assert.equal(readDraft(storage,key).note,'draft');removeDraft(storage,key,'draft');assert.equal(readDraft(storage,key),null)
storeDraft(storage,key,'x'.repeat(MAX_NOTE+100),'');assert.equal(readDraft(storage,key).note.length,MAX_NOTE)
storage.setItem(key,JSON.stringify({version:1,note:'expired',base:'',at:Date.now()-8*86400000}));assert.equal(readDraft(storage,key),null)
storage.setItem(key,'bad json');assert.equal(readDraft(storage,key),null)
assert.equal(storeDraft({setItem(){throw Error('denied')}},key,'text',''),false)
assert.equal(readDraft(null,key),null)
const commands=navigationCommands([{id:7,title:'Robot Control',category:{title:'Hardware'}}],[{id:1,title:'Hardware'},{id:'javascript:alert(1)',title:'invalid'}])
assert.equal(matchCommands(commands,'robot hardware')[0].path,'/feed/7')
assert.equal(commands.filter(x=>x.id.startsWith('category-')).length,1)
assert.deepEqual(commands.filter(x=>x.path).map(x=>x.path),['/today','/all','/starred','/history','/category/1','/feed/7'])
assert.equal(commands.find(x=>x.id==='unread').unread,true)
assert.ok(commands.every(x=>!x.delete&&!x.post&&!x.task&&!x.markRead))
assert.equal(matchCommands(commands,'does not exist').length,0)
globalThis.location={origin:'https://reader.test'}
for(const url of ['javascript:alert(1)','data:image/png,x','https://a:b@image.test/x','https://reader.test/mf/proxy/x','https://image.test/x?api_key=a','https://image.test/x?auth=a','https://image.test/x?X-Amz-Signature=a','https://image.test/x?access_token=a','https://image.test/x?jwt=a','https://image.test/x?session_id=a'])assert.equal(safePublicImage(url),'',url)
assert.equal(safePublicImage('https://image.test/x?w=800'),'https://image.test/x?w=800')
console.log('Reading helpers: draft isolation, retention, recovery guard, navigation and safe image links passed')
