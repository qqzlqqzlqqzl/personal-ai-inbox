export function safePublicImage(value){
 try{const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return '';if(typeof location!=='undefined'&&url.origin===location.origin)return '';for(const key of url.searchParams.keys())if(/token|secret|signature|credential|password|auth|session|apikey|accesskey|jwt|^(key|sig)$/i.test(key.replace(/[^a-z0-9]/gi,'')))return '';return url.href}catch{return ''}
}
