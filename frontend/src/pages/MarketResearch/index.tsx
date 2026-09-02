import { useState } from "react";
import { Button, Card, Input, Typography } from "antd";
import { localMarketCalculation } from "../../api/marketResearch";
import ErrorNotice from "../../components/ErrorNotice";

const configs={
  sectors:{title:"板块相对表现",path:"/sectors/relative-performance",help:"粘贴已确认的本地价格序列；程序只使用共同交易日，并明确收益差不是 alpha。",sample:'{"reference_id":"index","series":[{"id":"index","label":"指数","price_basis":"index_price","prices":{"2026-01-02":"100","2026-01-03":"101"}},{"id":"stock","label":"个股","price_basis":"forward_adjusted","prices":{"2026-01-02":"10","2026-01-03":"11"}}]}'},
  funds:{title:"基金披露持仓重叠",path:"/funds/visible-overlap",help:"仅计算同披露期、已披露证券的可见重叠下限；不代表实时持仓。",sample:'{"left":{"fund_id":"A","report_end":"2025-12-31","weight_definition_confirmed":true,"holdings":[]},"right":{"fund_id":"B","report_end":"2025-12-31","weight_definition_confirmed":true,"holdings":[]}}'},
  market:{title:"市场复盘",path:"/market-reviews/context",help:"不同龙虎榜统计窗口不会相加；没有历史异动解释时会明确显示不可用。",sample:'{"requested_date":"2026-01-03","dragon_tiger":[],"limit_pool":[]}'},
} as const;
export default function MarketResearch({kind}:{kind:keyof typeof configs}){const config=configs[kind];const [raw,setRaw]=useState<string>(config.sample);const [result,setResult]=useState<Record<string,unknown>|null>(null);const [error,setError]=useState<unknown>(null);const run=async()=>{try{setError(null);setResult(await localMarketCalculation(config.path,JSON.parse(raw)));}catch(failure){setError(failure);}};return <><div className="page-heading"><div><div className="eyebrow">LOCAL MARKET RESEARCH</div><h1>{config.title}</h1><p>{config.help}</p></div></div><Card><Typography.Paragraph type="secondary">本页只执行本地计算，不会触发外部 API。外部数据须先由用户确认权限、范围与口径后获取。</Typography.Paragraph><Input.TextArea value={raw} onChange={event=>setRaw(event.target.value)} autoSize={{minRows:8,maxRows:18}} spellCheck={false}/><Button type="primary" style={{marginTop:12}} onClick={()=>void run()}>执行本地计算</Button></Card>{error&&<ErrorNotice error={error} retry={()=>void run()}/>} {result&&<Card title="结果与限制" style={{marginTop:18}}><pre style={{whiteSpace:"pre-wrap",margin:0}}>{JSON.stringify(result,null,2)}</pre></Card>}</>}
