"""Build bounded jobs with the existing Inbox prompts; never truncate input."""
import argparse
import hashlib
import json
from pathlib import Path
from batch_control import atomic_json, digest

TRANSLATION_FIDELITY = ('\n补充保真要求：只使用本条 title 和 excerpt 明确给出的信息；'
    '不得添加开源、免费、性能、许可、兼容性等未提供的属性。'
    '标题中的型号、单字母和双关语无法确定含义时保留原词，不能猜译。'
    '保留数字、单位、日期和版本号；不要把可能、计划或厂商声称改成已证实。'
    '逐项保留时间、条件、否定及限定范围，不把当前限制移到未来，不把临近发生写成已经发生。'
    '局部不需要不能写成完全不需要；速度增加与耗时降低的百分比不得互换。'
    '技术术语须与原文含义等价，不能添加架构、系统能力或因果关系。'
    '不确定品牌的官方中文名时保留英文。双关或单字母短语可保留原文或省去修辞，不能猜解。'
    'excerpt可能在句中截断，不要补齐尾句或据常识推出未提供的结论。')

ANALYSIS_FIDELITY=('evidence只从content字段逐字复制连续8到120字符，选择长度合适的原句或原句连续片段；'
    '不能删去中间词语，不能为缩短句子补句号、省略号或其他原文没有的标点。'
    '输出前核对引句确实连续出现在content中，并保留正文中的时间、条件和不确定性。')

def build(sample, versions, runtime_sha256, runtime_source, context_size=65536, attempt=1,
          model_dataset=None, runtime_dataset=None):
    file = versions['file']
    model = {'model_repo':versions['model_repo'],'model_revision':versions['model_revision'],
             'filename':file['rfilename'],'size':file['size'],'sha256':file['lfs']['sha256'],
             'llama_commit':versions['llama_commit']}
    items=[]
    for row in sample['samples']:
        if row.get('skip_analysis'):
            continue
        # source_text is the exact captured model input; mark any pre-existing truncation.
        content={'title':row['title'],'url':row['url'],'content_source':row['content_source'],
                 'truncated':bool(row['truncated']),'content':row['source_text'],
                 'output_requirements':ANALYSIS_FIDELITY}
        if row.get('attempts',0)>0:
            content['retry_attempt']=row['attempts']+1
            content['retry_requirements']=('重新按同一评分标准判断。evidence请选择content中更短的一段连续原文，'
                '8到120字符，逐字复制；不要替换主语或同义词，不要省略中间的品牌名，'
                '不要转换大小写、直引号或弯引号。输出前逐字核对。')
        messages=[{'role':'system','content':sample['settings']['prompt']},
                  {'role':'user','content':json.dumps(content,ensure_ascii=False)}]
        refs={k:row[k] for k in ('entry_id','user_id','content_hash','source_text','truncated')}
        if row.get('upstream_hash'):
            refs['upstream_hash']=row['upstream_hash']
        if row.get('fulltext_receipt'):
            refs['fulltext_receipt']=row['fulltext_receipt']
        item={'id':'analysis-'+str(row['entry_id']),'kind':'analysis','messages':messages,
              'max_tokens':sample['settings']['max_output_tokens'],'source_refs':[refs]}
        item['input_hash']=digest({'messages':messages,'source_refs':[refs],'model':model})
        items.append(item)
    cards=[{**row['card'],'entry_id':row['entry_id'],'user_id':row['user_id'],
            **({'upstream_hash':row['upstream_hash']} if row.get('upstream_hash') else {})}
           for row in sample['samples'] if row.get('card')]
    for index in range(0,len(cards),6):
        rows=cards[index:index+6]
        payload={'output_requirements':'只概括明确完整的陈述，不补全截断句。作者署名、发布日期、阅读时长是页面元数据，不能移作事件或测试的时间。first showcase是展示列表的第一项，不表示首次公开。死亡不等于遇害。不得添加程度词、目的或未给出的因果关系。简介用完整句结尾，不保留残词或半句。',
                 'items':[{'id':row['entry_id'],'title':row['original_title'],
                          'excerpt':row['excerpt'],'source_kind':row['source_kind']} for row in rows]}
        messages=[{'role':'system','content':sample['translation_prompt']+TRANSLATION_FIDELITY},
                  {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
        item={'id':'cards-'+str(index//6),'kind':'translation','messages':messages,
              'max_tokens':min(2200,400*len(rows)),'source_refs':rows}
        item['input_hash']=digest({'messages':messages,'source_refs':rows,'model':model})
        items.append(item)
    manifest={'model':model,'runtime_sha256':runtime_sha256,'runtime_source':runtime_source,
              'context_size':context_size,'session_timeout':5400,'attempt':attempt,'items':items,
              'prompt_policy':'source-fidelity-v3',
              'generation':{'thinking':True,'reasoning_budget':-1,'use_remaining_context':True,
                            'temperature':1.0,'top_p':0.95,'top_k':20,'min_p':0.0,
                            'presence_penalty':1.5,'repeat_penalty':1.0,'seed':42}}
    if model_dataset:
        manifest.update(dataset_sources=[model_dataset],require_model_cache=True)
    if runtime_dataset:
        manifest.setdefault('dataset_sources',[]).append(runtime_dataset)
        manifest['runtime_dataset_source']=runtime_dataset
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sample',required=True)
    parser.add_argument('--versions',required=True)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--runtime-source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--context-size',type=int,default=65536)
    parser.add_argument('--model-dataset',help='Attach a verified model cache and disable cold downloads')
    args=parser.parse_args()
    with Path(args.runtime).open('rb') as stream:
        runtime_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest=build(json.loads(Path(args.sample).read_text(encoding='utf-8')),
        json.loads(Path(args.versions).read_text(encoding='utf-8')),runtime_hash,args.runtime_source,
        context_size=args.context_size,attempt=args.attempt,model_dataset=args.model_dataset)
    atomic_json(Path(args.output),manifest)
    print(json.dumps({'jobs':len(manifest['items']),'runtime_sha256':runtime_hash}))
