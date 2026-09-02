import { downloadFile, request } from "./client";
export interface LibraryItem { id:string; question:string; version:number; coverage:string; output_integrity:string; created_at:string; thscode:string|null; labels:string[] }
export interface DeletionImpact { report_id:string; exists:boolean; attachments:number; excerpts:number; notes:number; exports:number; blocked_by:string[] }
export const listReports=(q?:string)=>request<{items:LibraryItem[]}>({url:"/library/reports",method:"GET",params:q?{q}:undefined});
export const reportVersions=(id:string)=>request<{items:LibraryItem[]}>({url:`/library/reports/${id}/versions`,method:"GET"});
export const reportVersionDiff=(older:string,newer:string)=>request<import("../components/ReportVersionDiff").VersionDiff>({url:`/library/reports/${older}/versions/${newer}/diff`,method:"GET"});
export const createExport=(report_id:string,format:"markdown"|"html"|"json"|"csv"|"source_markdown")=>request<{export:{id:string;filename:string}}>({url:"/exports",method:"POST",data:{report_id,format}});
export const downloadExport=(id:string)=>downloadFile(`/exports/${id}/download`);
export const previewDeletion=(report_ids:string[])=>request<{items:DeletionImpact[]}>({url:"/library/deletion-preview",method:"POST",data:{report_ids}});
export const deleteReports=(report_ids:string[])=>request<{items:DeletionImpact[]}>({url:"/library/delete",method:"POST",data:{report_ids,confirmed:true}});
export const setReportLabels=(reportId:string,labels:string[])=>request<{labels:string[]}>({url:`/library/reports/${reportId}/labels`,method:"PUT",data:{labels}});
