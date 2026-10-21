import fs from "node:fs/promises";
import { Presentation } from "@oai/artifact-tool";
const rows=(await fs.readFile(new URL("../data/pilot_validation_curve.csv",import.meta.url),"utf8")).trim().split(/\r?\n/).slice(1).map(r=>r.split(","));
const p=Presentation.create({slideSize:{width:960,height:540}});const s=p.slides.add();s.background.fill="#fff";
s.charts.add("line",{position:{left:24,top:20,width:912,height:500},categories:rows.map(r=>r[0]),series:[{name:"mAP@0.50",values:rows.map(r=>Number(r[1])),line:{style:"solid",fill:"#B0161B",width:2}},{name:"mAP@0.50:0.95",values:rows.map(r=>Number(r[2])),line:{style:"solid",fill:"#3E596D",width:2}}],lineOptions:{smooth:false},hasLegend:true,legend:{position:"bottom"},xAxis:{title:"Training epoch"},yAxis:{title:"Validation mAP",min:0,max:1,numberFormatCode:"0%"}});
const b=await p.export({slide:s,format:"png",scale:1});await fs.writeFile(new URL("../charts/pilot_validation_curve.png",import.meta.url),new Uint8Array(await b.arrayBuffer()));
