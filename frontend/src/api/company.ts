import { request } from "./client";
export interface QualityMetric { metric:string; value:string|null; note:string|null }
export interface QualityDimension { id:string; label:string; status:"available"|"no_data"|"not_applicable"; metrics:QualityMetric[]; notes:string[] }
export interface CompanyResearch { identity:{name:string}; evidence_ids:string[]; quality:{dimensions:QualityDimension[]} }
export interface ComparisonCell { value:string|null; status:string; note:string|null }
export interface ComparisonResult { common_period:string|null; selected_count:number; comparable_count:number; gaps:string[]; rankings:Record<string,string[]>; rows:{company:{thscode:string;name:string;period:string|null};cells:Record<string,ComparisonCell>}[] }
export interface WatchItem { id:string; security_id:string; thscode:string; name:string; tags:string[] }
export interface Thesis { id:string; security_id:string; content:string; status:string; required_evidence:string[]; counter_evidence:string[]; observation_metrics:string[] }
export const companyResearch=(thscode:string,industry?:string)=>request<CompanyResearch>({url:`/companies/${thscode}/research`,method:"GET",params:industry?{industry}:undefined});
export const compareCompanies=(thscodes:string[])=>request<{comparison:ComparisonResult}>({url:"/companies/compare",method:"POST",data:{thscodes}});
export const listWatchlist=()=>request<{items:WatchItem[]}>({url:"/watchlist",method:"GET"});
export const addWatchlist=(input:Omit<WatchItem,"id">)=>request<{item:WatchItem}>({url:"/watchlist",method:"POST",data:input});
export const removeWatchlist=(id:string)=>request<{deleted:true}>({url:`/watchlist/${id}`,method:"DELETE"});
export const listTheses=(securityId?:string)=>request<{items:Thesis[]}>({url:"/theses",method:"GET",params:securityId?{security_id:securityId}:undefined});
export const addThesis=(input:Omit<Thesis,"id"|"status"> & {confirmed_by_user:boolean})=>request<{item:Thesis}>({url:"/theses",method:"POST",data:input});
export const updateThesis=(id:string,input:{content:string;revoked?:boolean})=>request<{item:Thesis}>({url:`/theses/${id}`,method:"PUT",data:input});
