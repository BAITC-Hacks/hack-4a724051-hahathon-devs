import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire('C:/Users/tarih/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json');
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const out=new URL('../test-files/assistant/',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1');
const qa=new URL('../tmp/fixture-qa/',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1');
await fs.mkdir(out,{recursive:true});await fs.mkdir(qa,{recursive:true});
for (const [file,sheets,rows] of [['04_spec_1200_rows.xlsx',3,400],['05_spec_22_sheets.xlsx',22,4]]) {
  const wb=Workbook.create();
  for(let n=1;n<=sheets;n++) {
    const sheet=wb.worksheets.add(`Раздел ${n}`);
    const values=[['Строка','Артикул','Количество','Контрольная метка']];
    for(let i=1;i<=rows;i++) values.push([(n-1)*rows+i,i%2?'990100001_':'990100003_',i%3+1,`SHEET-${n}-ROW-${i}`]);
    sheet.getRange(`A1:D${values.length}`).values=values;
    sheet.getRange(`A1:D${values.length}`).format.rowHeight=22;
    sheet.getRange('A1:D1').format.fill='#DCEAF4';
    sheet.getRange('A1:D1').format.font.bold=true;
    sheet.getRange('A:A').format.columnWidth=12;
    sheet.getRange('B:B').format.columnWidth=20;
    sheet.getRange('C:C').format.columnWidth=15;
    sheet.getRange('D:D').format.columnWidth=28;
    sheet.freezePanes.freezeRows(1);
    const preview=await wb.render({sheetName:sheet.name,range:`A1:D${Math.min(9,values.length)}`,scale:1,format:'png'});
    await fs.writeFile(`${qa}/${file}-${n}.png`,new Uint8Array(await preview.arrayBuffer()));
  }
  wb.recalculate();
  await (await SpreadsheetFile.exportXlsx(wb)).save(`${out}/${file}`);
  // Keep the upload folder limited to the ten actual test files.
  const sidecar = `${out}/${file}.inspect.ndjson`;
  try {
    await fs.copyFile(sidecar, `${qa}/${file}.inspect.ndjson`);
    await fs.unlink(sidecar);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  console.log(`Exported ${file}: ${sheets} sheets, ${sheets*rows} data rows`);
}
