import {pairing} from '../api/pairing.js';
export const pairingStore={studies:[],runs:[],studyId:0,runId:0,readings:[],draft:{}};
export async function loadPairing(){[pairingStore.studies,pairingStore.runs]=await Promise.all([pairing.listStudies(),pairing.listRuns()]);if(!pairingStore.studyId)pairingStore.studyId=pairingStore.studies[0]?.id||0;}
export async function loadReadings(){pairingStore.readings=pairingStore.studyId?await pairing.readings(pairingStore.studyId):[];}
