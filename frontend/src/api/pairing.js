import {request,post} from './client.js';
export const pairing={
 listStudies:()=>request('/pairing/studies'),
 createStudy:x=>post('/pairing/studies',x),
 readings:id=>request(`/pairing/studies/${id}/readings`),
 correct:(id,x)=>post(`/pairing/studies/${id}/corrections`,x),
 listRuns:()=>request('/pairing/runs'),
 getRun:id=>request(`/pairing/runs/${id}`),
 createRun:(id,x)=>post(`/pairing/studies/${id}/runs`,x),
 review:(id,note)=>post(`/pairing/runs/${id}/review`,{note}),
};
