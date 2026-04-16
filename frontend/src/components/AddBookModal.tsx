import { useState, type CSSProperties } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { Btn } from "./Btn";
import { Spin } from "./Spin";

export function AddBookModal({onClose,onAdded}:any){const t=useTheme();const[f,setF]=useState<any>({title:"",author_name:"",series_name:"",series_index:"",pub_date:"",expected_date:"",description:"",isbn:"",is_unreleased:false});const[saving,setSaving]=useState(false);const[err,setErr]=useState("");
const save=async()=>{if(!f.title||!f.author_name){setErr("Title and author are required");return}setSaving(true);try{await api.post("/discovery/books/add",f);onAdded&&onAdded();onClose()}catch{setErr("Failed to add")}setSaving(false)};
const upF=(field:string,val:any)=>setF((prev:any)=>({...prev,[field]:val}));
const ist:CSSProperties={padding:"8px 10px",background:t.inp,border:`1px solid ${t.border}`,borderRadius:6,color:t.text2,fontSize:13,width:"100%"};
const lbl:CSSProperties={fontSize:11,fontWeight:600,color:t.tg,textTransform:"uppercase"};
return<div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.5)",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",animation:"fadeOverlay 0.2s ease-out"}} onClick={onClose}><div onClick={e=>e.stopPropagation()} className="modal-panel" style={{background:t.bg2,border:`1px solid ${t.border}`,borderRadius:12,padding:24,animation:"fadeIn 0.2s ease-out",width:460,maxWidth:"90vw",maxHeight:"80vh",overflowY:"auto",display:"flex",flexDirection:"column",gap:14}}>
<h2 style={{fontSize:18,fontWeight:700,color:t.text,margin:0}}>Add Book</h2>
<div style={{display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Title *</label><input value={f.title} onChange={e=>upF("title",e.target.value)} style={ist}/></div>
<div style={{display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Author *</label><input value={f.author_name} onChange={e=>upF("author_name",e.target.value)} style={ist}/></div>
<div style={{display:"flex",gap:10}}><div style={{flex:2,display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Series</label><input value={f.series_name} onChange={e=>upF("series_name",e.target.value)} style={ist}/></div><div style={{flex:1,display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>#</label><input type="number" value={f.series_index} onChange={e=>upF("series_index",e.target.value)} style={ist}/></div></div>
<div style={{display:"flex",gap:10}}><div style={{flex:1,display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Pub date</label><input type="date" value={f.pub_date} onChange={e=>upF("pub_date",e.target.value)} style={ist}/></div><div style={{flex:1,display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Expected date</label><input type="date" value={f.expected_date} onChange={e=>upF("expected_date",e.target.value)} style={ist}/></div></div>
<div style={{display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>ISBN</label><input value={f.isbn} onChange={e=>upF("isbn",e.target.value)} style={ist}/></div>
<div style={{display:"flex",flexDirection:"column",gap:4}}><label style={lbl}>Description</label><input value={f.description} onChange={e=>upF("description",e.target.value)} style={ist}/></div>
<div style={{display:"flex",alignItems:"center",gap:8}}><input type="checkbox" checked={f.is_unreleased} onChange={e=>upF("is_unreleased",e.target.checked)}/><label style={{fontSize:13,color:t.text2}}>Unreleased / Upcoming</label></div>
{err?<div style={{color:t.redt,fontSize:12}}>{err}</div>:null}
<div style={{display:"flex",gap:8,justifyContent:"flex-end"}}><Btn variant="ghost" onClick={onClose}>Cancel</Btn><Btn variant="accent" onClick={save} disabled={saving}>{saving?<Spin/>:"Add Book"}</Btn></div>
</div></div>}
