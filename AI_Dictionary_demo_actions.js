/* 모바일·PC 공통: 데모 데이터에 근거한 후속 질문과 파일 작업 */
let activeScenario=null;
const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const plain=value=>String(value).replace(/<[^>]*>/g,'');
const rootScenario=S=>S.origin||S;
function followAction(S,label){
  const root=rootScenario(S), items=S.results||[];
  const all=S.includeTrash
    ? (root.trashResults||root.results||[]).map(r=>({...r,inTrash:true}))
    : root.results||[];
  const filter=(predicate,notice)=>({items:items.filter(predicate),notice});
  const detail=(title,html)=>({detail:{title,html}});
  switch(label){
    case '올해 것만 보기': case '올해 것만': return filter(r=>r.date.startsWith('2026'),'2026년 결과만 보여드려요');
    case '창밖이 보이는 것만': return filter(r=>!r.sub,'창밖 바다가 보이는 사진만 남겼어요');
    case '이 카페 이름 알려줘': {
      const dates=new Set(items.map(r=>r.date.slice(0,10).replaceAll('.','-')));
      const visits=DEMO_DATA.cafe.visits.filter(v=>dates.has(v.date));
      return detail(label,'<p>사진을 찍은 카페를 방문 날짜별로 정리했어요.</p>'+visits.map(v=>'<p><b>'+h(v.location)+'</b><br>'+h(v.date)+' · '+h(v.situation)+'</p>').join(''));
    }
    case '오래된 순으로': return {items:[...items].sort((a,b)=>a.date.localeCompare(b.date)),notice:'촬영 날짜가 오래된 순서예요'};
    case '같이 신은 옷 보기': return detail(label,items.map(r=>{const photo=DEMO_DATA.shoe.photos.find(p=>p.filename===r.file);return '<p><b>'+h(r.date)+'</b><br>'+h(photo?.description||plain(r.why))+'</p><img class="detail-image" src="'+U(r.src)+'" alt="'+h(r.file)+'">';}).join(''));
    case '산 날짜 찾기': return detail(label,'<p>구매 영수증이나 주문 내역을 찾지 못했어요.</p><p>이 신발을 신은 가장 이른 사진은 <b>2023.04.02</b>예요. 구매일 자체는 확인할 수 없어요.</p>');
    case '금액 높은 순': return {items:[...items].sort((a,b)=>quoteAmount(b)-quoteAmount(a)),notice:'부가세 포함 금액이 높은 순서예요'};
    case '엑셀로 정리해줘': return {download:true};
    case '다른 회의 자료 보기': {
      const selected=new Set(all.map(r=>r.file));
      const meetingScenario=SCENARIOS.find(s=>s.q==='지난주 회의 때 받은 자료');
      const other=meetingScenario.steps.find(step=>step.opts).opts.flatMap(option=>option[2].results)
        .filter(r=>!selected.has(r.file))
        .map(r=>{const info=DEMO_DATA.meeting.files.find(f=>f.filename===r.file);return {...r,why:'<em>'+h(info.meeting_name)+'</em> · '+r.why};})
        .sort((a,b)=>b.date.localeCompare(a.date));
      return {items:other,notice:'선택한 회의를 제외한 다른 회의 자료예요. 파일마다 회의 이름을 표시했어요.'};
    }
    case '이 회의 대화 내용 보기': return detail('회의 자료 공유 내역','<p>대화 원문은 없지만, 자료를 공유한 시각과 담당자는 확인할 수 있어요.</p>'+items.map(r=>{const f=DEMO_DATA.meeting.files.find(f=>f.filename===r.file);return '<p><b>'+h(f?.shared_at.replace('T',' ').slice(0,16))+' · '+h(f?.shared_by_role)+'</b><br>'+h(r.file)+'<br>'+h(f?.description)+'</p>';}).join(''));
    case '한 폴더로 묶기': return {folder:true};
    case '2024년 것만': return filter(r=>r.date.startsWith('2024'),'2024년 방문 사진이에요');
    case '2023년 것만': return filter(r=>r.date.startsWith('2023'),'2023년 방문 사진이에요');
    case '인스타그램에 올린 것만': return filter(r=>r.store==='인스타그램','인스타그램에 올린 사진만 보여드려요');
    case '송년회 것만': return filter(r=>r.date.startsWith('2025.12.19'),'작년 송년회 사진만 보여드려요');
    case '와인 이름 정리해줘': {
      const photos=DEMO_DATA.wine.photos.filter(p=>items.some(r=>r.file===p.filename));
      const ids=new Set(photos.flatMap(p=>p.visible_wine_ids));
      return detail('사진 속 와인 이름','<p>사진 속 라벨에서 확인한 와인 이름이에요.</p>'+DEMO_DATA.wine.wines.filter(w=>ids.has(w.wine_id)).map(w=>'<p><b>'+h(w.name)+' '+h(w.vintage||w.designation)+'</b><br>'+h(w.type)+' · '+h(w.designation)+'</p>').join(''));
    }
    case '같은 와인이 또 있나': {
      const photos=DEMO_DATA.wine.photos.filter(p=>items.some(r=>r.file===p.filename));
      const ids=new Set(photos.flatMap(p=>p.visible_wine_ids));
      const other=DEMO_DATA.wine.photos.filter(p=>!items.some(r=>r.file===p.filename)&&p.visible_wine_ids.some(id=>ids.has(id)));
      if(other.length) return {items:all.filter(r=>other.some(p=>p.filename===r.file)),notice:'같은 와인이 보이는 다른 사진이에요'};
      return detail(label,'<p>다른 이벤트에서 같은 와인을 찍은 사진은 찾지 못했어요.</p>'+DEMO_DATA.wine.wines.filter(w=>ids.has(w.wine_id)).map(w=>'<p><b>'+h(w.name)+'</b> · 현재 사진 '+photos.filter(p=>p.visible_wine_ids.includes(w.wine_id)).length+'장에 등장</p>').join(''));
    }
    case '게시글 원문 보기': {const e=DEMO_DATA.party.text_evidence;return detail('페이스북 게시글','<p>'+h(e.posted_at.replace('T',' ').slice(0,16))+'</p><blockquote>'+h(e.caption)+'</blockquote>');}
    case '올리지 않은 것만': return filter(r=>!['페이스북','인스타그램'].includes(r.store),'SNS에 올리지 않은 사진만 보여드려요');
    case '그날 사진 전부 보기': return {items:all,notice:'그날 찾은 사진 6장을 모두 보여드려요.'};
    case 'v7만 열기': return {preview:all.find(r=>r.file.includes('_v7'))};
    case 'v6와 달라진 점': return detail('v7과 v6 비교','<div class="comparison">'+DEMO_DATA.brand.comparison.differences.map(d=>'<p><b>'+h(d.pages)+'</b><br><strong>v7</strong> '+h(d.v7)+'<br><strong>v6</strong> '+h(d.v6)+'</p>').join('')+'</div>');
    case '같은 폴더 보기': return {items:all.filter(r=>r.ext==='pptx'),notice:'브랜드전략 폴더의 편집 가능한 버전 2개예요'};
    case '휴지통에서 찾기': return {
      items:(S.trashResults||[]).map(r=>({...r,inTrash:true})),
      includeTrash:true,
      notice:Array.isArray(S.trashResults)
        ? (S.trashResults.length?'모바일·PC·메일함 휴지통을 검색했어요. 휴지통에 있는 파일만 표시합니다.':'휴지통에도 검색 조건에 맞는 결과가 없어요.')
        : '이 데모에는 휴지통 데이터가 없어 휴지통 검색 결과를 확인할 수 없어요.'
    };
    case '이전 결과로 돌아가기': return {restore:S.previous||root};
    default: throw new Error('연결되지 않은 후속 질문: '+label);
  }
}
function availableFollow(S){
  if(!S.results.length) return S.includeTrash?[]:['휴지통에서 찾기'];
  return S.follow.filter(label=>{
    if((S.usedFollow||[]).includes(label)) return false;
    const action=followAction(S,label);
    if(!action.items) return true;
    // 현재 결과와 같거나 결과가 없는 필터/정렬은 추천하지 않는다.
    return action.items.length>0 && (action.items.length!==S.results.length
      || action.items.some((item,index)=>item.file!==S.results[index].file));
  });
}
function quoteAmount(r){return DEMO_DATA.quotation.documents.find(d=>d.filename===r.file)?.total_amount||0;}
function openDetail(title,html){
  let dialog=document.getElementById('demo-detail');
  if(!dialog){dialog=document.createElement('dialog');dialog.id='demo-detail';dialog.className='demo-detail';document.body.appendChild(dialog);dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});}
  dialog.innerHTML='<div class="detail-head"><h2>'+h(title)+'</h2><button aria-label="닫기">×</button></div><div class="detail-body">'+html+'</div>';
  dialog.querySelector('button').onclick=()=>dialog.close();
  if(!dialog.open)dialog.showModal();
}
function filePath(r){return r.src||(r.shot?r.thumb:r.thumb.slice(0,r.thumb.lastIndexOf('/')+1)+r.file);}
function previewFile(r){
  openDetail(r.file,'<p>'+(r.inTrash?'휴지통 · ':'')+h(r.store)+' · '+h(r.date)+'</p><p>'+r.why+'</p><img class="detail-image" src="'+U(r.src||r.thumb)+'" alt="'+h(r.file)+'"><p><a class="detail-link" href="'+U(filePath(r))+'" download="'+h(r.file)+'">원본 파일 다운로드</a></p>');
}
function applyFollow(S,label,result){
  if(busy) return;
  if(label==='이전 결과로 돌아가기'){
    completeFollow(S,label,result);
    return;
  }
  busy=true; inp.disabled=true; send.disabled=true;
  const buttons=[...result.querySelectorAll('button')];
  buttons.forEach(button=>button.disabled=true);
  result.setAttribute('aria-busy','true');
  const status=document.createElement('div');
  status.className='result-notice';
  status.setAttribute('role','status');
  status.innerHTML='<span class="hop" aria-hidden="true"><i></i><i></i><i></i></span> '
    +(label==='휴지통에서 찾기'?'모바일·PC·메일함 휴지통에서 찾고 있어요…':'‘'+h(label)+'’ 요청을 처리하고 있어요…');
  result.prepend(status);
  wait(label==='휴지통에서 찾기'?1600:1000,()=>{
    try{
      completeFollow(S,label,result);
    } finally {
      status.remove();
      result.removeAttribute('aria-busy');
      buttons.forEach(button=>button.disabled=false);
      busy=false; inp.disabled=false; send.disabled=false;
    }
  });
}
function completeFollow(S,label,result){
  const action=followAction(S,label);
  const usedFollow=[...new Set([...(S.usedFollow||[]),label])];
  // 결과 전환 전의 칩 상태는 보존하고, 상세/다운로드 작업만 현재 상태에 기록한다.
  if(!action.restore && !action.items) S.usedFollow=usedFollow;
  if(action.restore)results(action.restore,false,result);
  else if(action.detail)openDetail(action.detail.title,action.detail.html);
  else if(action.preview)previewFile(action.preview);
  else if(action.download){downloadQuotes(S.results);openDetail('엑셀로 정리했어요','<p>현재 결과 '+S.results.length+'건의 파일명, 날짜, 출처, 부가세 포함 금액을 엑셀 파일로 저장했어요.</p>');}
  else if(action.folder)openDetail('회의 자료 폴더','<p>선택한 자료 '+S.results.length+'개를 모았어요. 각 파일을 다운로드할 수 있어요.</p><div class="folder-files">'+S.results.map(r=>'<a href="'+U(filePath(r))+'" download="'+h(r.file)+'">▱ '+h(r.file)+'</a>').join('')+'</div>');
  else if(action.items){results({...rootScenario(S),origin:rootScenario(S),previous:S,usedFollow,includeTrash:S.includeTrash||action.includeTrash||false,results:action.items,notice:action.notice},false,result);}
}
/* 라이브러리 없이 생성하는 표준 XLSX: ZIP 컨테이너와 Office Open XML */
function makeWorkbook(rows){
  const xml=s=>h(s).replace(/&#39;/g,'&apos;');
  const sheet='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows.map((row,i)=>'<row r="'+(i+1)+'">'+row.map((v,k)=>'<c r="'+String.fromCharCode(65+k)+(i+1)+'"'+(typeof v==='number'?'><v>'+v+'</v>':' t="inlineStr"><is><t>'+xml(v)+'</t></is>')+'</c>').join('')+'</row>').join('')+'</sheetData></worksheet>';
  const files={
    '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="견적서 검색 결과" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    'xl/worksheets/sheet1.xml':sheet
  };
  const encoder=new TextEncoder(), parts=[],central=[];let offset=0;
  const crc32=bytes=>{let crc=0xffffffff;for(const b of bytes){crc^=b;for(let i=0;i<8;i++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}return (crc^0xffffffff)>>>0;};
  for(const [name,content] of Object.entries(files)){
    const n=encoder.encode(name),data=encoder.encode(content),crc=crc32(data);
    const local=new Uint8Array(30+n.length),lv=new DataView(local.buffer);
    lv.setUint32(0,0x04034b50,true);lv.setUint16(4,20,true);lv.setUint32(14,crc,true);lv.setUint32(18,data.length,true);lv.setUint32(22,data.length,true);lv.setUint16(26,n.length,true);local.set(n,30);
    parts.push(local,data);
    const entry=new Uint8Array(46+n.length),cv=new DataView(entry.buffer);
    cv.setUint32(0,0x02014b50,true);cv.setUint16(4,20,true);cv.setUint16(6,20,true);cv.setUint32(16,crc,true);cv.setUint32(20,data.length,true);cv.setUint32(24,data.length,true);cv.setUint16(28,n.length,true);cv.setUint32(42,offset,true);entry.set(n,46);central.push(entry);offset+=local.length+data.length;
  }
  const size=central.reduce((n,p)=>n+p.length,0),end=new Uint8Array(22),ev=new DataView(end.buffer);
  ev.setUint32(0,0x06054b50,true);ev.setUint16(8,central.length,true);ev.setUint16(10,central.length,true);ev.setUint32(12,size,true);ev.setUint32(16,offset,true);
  return new Blob([...parts,...central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
function downloadQuotes(items){
  const blob=makeWorkbook([['파일명','날짜','출처','부가세 포함 금액 (원)'],...items.map(r=>[r.file,r.date,r.store,quoteAmount(r)])]);
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='견적서_검색결과.xlsx';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
