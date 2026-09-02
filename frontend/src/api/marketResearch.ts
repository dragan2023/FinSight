import { request } from "./client";
export const localMarketCalculation=(path:string,data:unknown)=>request<Record<string,unknown>>({url:path,method:"POST",data});
