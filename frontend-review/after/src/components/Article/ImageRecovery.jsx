import {useEffect,useState} from 'react'
import {useStore} from '@nanostores/react'
import {activeContentState} from '@/store/contentState'
import '../Ai/ReviewWorkflows.css'

import {safePublicImage} from './safe-image-url'
export function ImageRecoveryNotice({src,alt='',attempts,retry}){
 const entry=useStore(activeContentState),image=safePublicImage(src),source=safePublicImage(entry?.url)
 return <div className="review-image-error" role="group" aria-label="图片加载恢复"><strong>图片未能加载</strong><p>{alt||'原文图片暂时不可用，文字内容仍可阅读。'}</p><div><button type="button" disabled={attempts>=3} onClick={retry}>{attempts>=3?'已达本次重试上限':'重试这张图片'}</button>{image&&<a href={image} target="_blank" rel="noopener noreferrer">打开原图 ↗</a>}{source&&<a href={source} target="_blank" rel="noopener noreferrer">在原文查看 ↗</a>}</div><small>已手动重试 {attempts} / 3 次；不会无限自动请求。</small></div>
}
export default function RecoverableImage(props){
 const [failed,setFailed]=useState(false),[attempts,setAttempts]=useState(0)
 useEffect(()=>{setFailed(false);setAttempts(0)},[props.src])
 if(failed)return <ImageRecoveryNotice src={props.src} alt={props.alt} attempts={attempts} retry={()=>{if(attempts<3){setAttempts(n=>Math.min(3,n+1));setFailed(false)}}}/>
 return <img {...props} key={String(props.src)+':'+attempts} onError={()=>setFailed(true)}/>
}
