import React, {useEffect, useState} from 'react';
import {api, type User} from './authentication';
import './research.css';

type Document = {id:string; title:string; characters:number; sha256:string};
const labels:Record<string,string>={bm25:'Keyword search',dense:'Semantic search',hybrid:'Combined search'};

export function DocumentLibrary({user,onLaunch}:{user:User;onLaunch:(run:any)=>void}){
 const [documents,setDocuments]=useState<Document[]>([]),[selected,setSelected]=useState<string[]>([]);
 const [title,setTitle]=useState(''),[text,setText]=useState(''),[question,setQuestion]=useState('');
 const [models,setModels]=useState<string[]>([]),[model,setModel]=useState(''),[strategy,setStrategy]=useState('bm25'),[strategies,setStrategies]=useState(['bm25']);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[preview,setPreview]=useState<any>(null);
 const writable=user.role!=='viewer';
 async function refresh(){const rows=await api('/documents');setDocuments(rows);setSelected(ids=>ids.filter(id=>rows.some((d:Document)=>d.id===id)));}
 useEffect(()=>{refresh().catch(e=>setError(e.message));api('/models').then(data=>{setModels(data.models);setModel(['qwen3:1.7b'].find(name=>data.models.includes(name))||data.models[0]||'');}).catch(e=>setError(e.message));api('/research/config').then(data=>setStrategies(data.strategies)).catch(e=>setError(e.message));},[]);
 async function add(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');setMessage('');try{
  const doc=await api('/documents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,text})});
  await refresh();setSelected(ids=>Array.from(new Set([...ids,doc.id])));setTitle('');setText('');setMessage('Document saved privately and selected for research.');
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function remove(doc:Document){if(!window.confirm('Remove '+doc.title+' from your library? Earlier research runs retain their captured text and evidence.'))return;
  setBusy(true);setError('');try{await api('/documents/'+doc.id,{method:'DELETE'});if(preview?.id===doc.id)setPreview(null);await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function readFile(file?:File){if(!file)return;setError('');try{
  if(!/\.(txt|md)$/i.test(file.name)||file.size>60000)throw new Error('Choose a UTF-8 .txt or .md file smaller than 60 KB.');
  const content=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
  if(content.length>40000||content.includes('\0'))throw new Error('Use readable text up to 40,000 characters.');
  setText(content);setTitle(file.name.replace(/\.(txt|md)$/i,''));
 }catch(e){setError((e as Error).message);}}
 async function ask(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{
  const run=await api('/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:question,mode:'ollama',model,agent:'analyst',approval_required:true,enabled_tools:[],research_mode:true,document_ids:selected,retrieval_strategy:strategy})});onLaunch(run);
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="document-workspace" aria-label="Document research">
  <div className="research-intro"><h2>Answers you can trace.</h2><p>Choose your documents, ask a question, then inspect the supporting passages. Research stays within these documents and uses your workspace’s local AI.</p></div>
  {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status" className="info">{message}</p>}
  <div className="document-columns"><section><h3>Your library <span className="badge">{documents.length} / 30</span></h3>
   {documents.length?<div className="document-list">{documents.map(doc=><article className="document-row" key={doc.id}>
    <label className="document-select"><input type="checkbox" checked={selected.includes(doc.id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,doc.id]:ids.filter(id=>id!==doc.id))}/><span><strong>{doc.title}</strong><small>{doc.characters.toLocaleString()} characters</small></span></label>
    <div className="document-controls"><button className="secondary" disabled={busy} onClick={()=>api('/documents/'+doc.id).then(setPreview).catch(e=>setError(e.message))}>Read</button>{writable&&<button className="secondary" disabled={busy} onClick={()=>remove(doc)}>Remove</button>}</div>
   </article>)}</div>:<p className="library-empty">Add a document to begin. Try a handbook, project specification, or research notes.</p>}
   {writable&&<details className="document-add" open={!documents.length}><summary>Add a document</summary><form onSubmit={add}>
    <label>Import text<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={e=>{readFile(e.target.files?.[0]);e.target.value='';}}/></label>
    <label>Document title<input required maxLength={160} value={title} onChange={e=>setTitle(e.target.value)}/></label>
    <label>Document text<textarea required minLength={50} maxLength={40000} rows={8} value={text} onChange={e=>setText(e.target.value)} placeholder="Paste at least 50 characters of source text…"/></label>
    <p className="research-note">UTF-8 text or Markdown, up to 40,000 characters and 60 KB. Saved documents are private to your account. PDFs and images are not supported.</p>
    <button className="primary" disabled={busy||!title.trim()||text.trim().length<50}>Save document</button>
   </form></details>}
  </section><section className="research-question"><h3>Ask your documents</h3><form onSubmit={ask}>
   <label>Question<textarea required minLength={3} maxLength={4000} rows={5} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="What does the source say? Ask a specific question…"/></label>
   <label>Local model<select required value={model} onChange={e=>setModel(e.target.value)}>{!models.length&&<option value="">Start Ollama to continue</option>}{models.map(name=><option key={name}>{name}</option>)}</select></label>
   <label>How to find passages<select value={strategy} onChange={e=>setStrategy(e.target.value)}>{strategies.map(name=><option key={name} value={name}>{labels[name]}</option>)}</select></label>
   <p className="research-note">{selected.length} document{selected.length===1?'':'s'} selected. The answer can decline when evidence is missing. You review the report before export.</p>
   <button className="primary" disabled={!writable||busy||!model||!selected.length||question.trim().length<3}>{busy?'Preparing…':'Research selected documents'}</button>
  </form><p className="research-note">A valid quote proves its source, not that the answer interpreted it correctly. Review the claims and passages together.</p></section></div>
  {preview&&<section className="document-preview"><div className="panel-title"><h3>{preview.title}</h3><button className="secondary" onClick={()=>setPreview(null)}>Close source</button></div><pre>{preview.text}</pre></section>}
 </section>;
}

export function ResearchEvidence({result}:{result:any}){
 if(!result.retrieval)return null;
 return <section className="research-evidence" aria-label="Answer evidence">
  <p className="evidence-status">{result.abstained?'Answer withheld':`Cited passages · ${labels[result.retrieval.strategy]||result.retrieval.strategy}`}</p>
  {result.reason==='invalid_citation_or_response'&&<p>The model’s draft failed citation validation, so Orbit withheld it. Try a more specific question or a stronger local model.</p>}
  <div className="research-usage"><span>Retrieval {(result.retrieval.retrieval_ms/1000).toFixed(2)}s</span><span>Total {(result.total_ms/1000).toFixed(2)}s</span><span>{result.usage.prompt_tokens+result.usage.completion_tokens} reported tokens</span></div>
  {result.claims?.map((claim:any,index:number)=><details key={index} className="claim-evidence"><summary>Evidence for claim {index+1}</summary><p>{claim.text}</p>{claim.citations.map((citation:any,j:number)=>{
   const hit=result.evidence.find((item:any)=>item.source_id===citation.source_id);
   return <div key={j}><strong>[{citation.source_id}] {hit?.title} · lines {hit?.line_start}–{hit?.line_end}</strong><blockquote>{citation.quote}</blockquote></div>;
  })}</details>)}
  <details className="claim-evidence"><summary>Inspect all retrieved passages</summary>{result.retrieval.hits.map((hit:any)=><article key={hit.id}><strong>[{hit.source_id}] {hit.title} · lines {hit.line_start}–{hit.line_end}</strong><pre>{hit.text}</pre></article>)}</details>
  <p className="research-note">{result.provenance_notice}</p>
 </section>;
}
