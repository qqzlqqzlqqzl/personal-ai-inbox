export function navigationCommands(feeds=[],categories=[]){
 const commands=[{id:'today',label:'今天的文章',detail:'保留当前 AI / 原始筛选',path:'/today'}, {id:'unread',label:'所有未读文章',detail:'只切换视图，不标记已读',path:'/all',unread:true},{id:'starred',label:'收藏文章',detail:'已收藏的阅读内容',path:'/starred'},{id:'history',label:'阅读历史',detail:'最近阅读的文章',path:'/history'},{id:'console',label:'AI 设置与资源看板',detail:'打开控制台，不启动任务',console:true}]
 for(const category of categories)if(Number.isSafeInteger(Number(category.id))&&Number(category.id)>0)commands.push({id:'category-'+category.id,label:category.title||'未命名分类',detail:'分类',path:'/category/'+Number(category.id)})
 for(const feed of feeds)if(Number.isSafeInteger(Number(feed.id))&&Number(feed.id)>0)commands.push({id:'feed-'+feed.id,label:feed.title||'未命名订阅',detail:'订阅 · '+(feed.category?.title||''),path:'/feed/'+Number(feed.id)})
 return commands
}
export function matchCommands(commands,query){const words=String(query||'').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);return commands.filter(c=>words.every(w=>(c.label+' '+c.detail).toLocaleLowerCase().includes(w)))}
