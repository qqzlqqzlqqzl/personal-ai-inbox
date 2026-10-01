export const MAX_NOTE=20000
const PREFIX='reader.note.draft:v1:'
export function draftKey(server,owner,entryId){return PREFIX+encodeURIComponent(JSON.stringify([String(server),String(owner),String(entryId)]))}
export function readDraft(storage,key){
 try{const value=JSON.parse(storage.getItem(key)||'null');if(!value||value.version!==1||typeof value.note!=='string'||value.note.length>MAX_NOTE||typeof value.base!=='string'||value.base.length>MAX_NOTE||!Number.isFinite(value.at)||Date.now()-value.at>7*86400000)return null;return value}catch{return null}
}
export function storeDraft(storage,key,note,base){try{storage.setItem(key,JSON.stringify({version:1,note:String(note).slice(0,MAX_NOTE),base:String(base).slice(0,MAX_NOTE),at:Date.now()}));return true}catch{return false}}
export function removeDraft(storage,key,expectedNote){try{if(expectedNote===undefined||readDraft(storage,key)?.note===expectedNote)storage.removeItem(key);return true}catch{return false}}
