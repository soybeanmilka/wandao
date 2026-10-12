const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const converterPath = path.join(repoRoot, 'plugins', 'yuque', 'backend', 'export_yuque.py');
const desktopRequire = createRequire(path.join(repoRoot, 'wandao_electron', 'package.json'));
const { parseHTML } = desktopRequire('linkedom');

function runConverter(content, title = 'Converter regression') {
  const source = fs.readFileSync(converterPath, 'utf8');
  const match = source.match(/YUQUE_CONVERTER_JS = r?"""([\s\S]*?)"""/);
  assert.ok(match, 'YUQUE_CONVERTER_JS was not found');
  const document = {
    implementation: {
      createHTMLDocument: () => parseHTML('<!doctype html><html><body></body></html>').document
    }
  };
  const converter = Function('document', `"use strict"; return (${match[1]});`)(document);
  return converter(content, title);
}

test('Yuque converter collects table images and attachments through a standards-based DOM', () => {
  const imageUrl = 'https://cdn.example.test/table.png';
  const attachmentUrl = 'https://files.example.test/guide.pdf';
  const cardValue = `data:${encodeURIComponent(JSON.stringify({ src: imageUrl, title: 'table image' }))}`;
  const content = [
    '<p><img src="https://cdn.example.test/paragraph.png" alt="paragraph image"></p>',
    '<table><tr>',
    `<td><p><card name="image" value="${cardValue}"></card></p></td>`,
    `<td><a href="${attachmentUrl}">guide.pdf</a></td>`,
    '<td>A | B</td>',
    '</tr></table>',
    `<p><card name="image" value="${cardValue}"></card></p>`
  ].join('');

  const result = runConverter(content);
  const tableImageResources = result.resources.filter((item) => item.url === imageUrl && item.kind === 'image');

  assert.match(result.markdown, new RegExp(`!\\[table image\\]\\(${imageUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\)`));
  assert.equal(tableImageResources.length, 2);
  assert.ok(result.resources.some((item) => item.url === attachmentUrl && item.kind === 'attachment'));
  assert.ok(result.resources.some((item) => item.url === 'https://cdn.example.test/paragraph.png' && item.kind === 'image'));
  assert.match(result.markdown, /A \\| B/);
});

function fileCard(name, data, encodeStyle = 'uri') {
  let value;
  if (encodeStyle === 'json-prefix') {
    value = `data:application/json,${encodeURIComponent(JSON.stringify(data))}`;
  } else if (encodeStyle === 'base64') {
    value = `data:application/json;base64,${Buffer.from(JSON.stringify(data), 'utf8').toString('base64')}`;
  } else {
    value = `data:${encodeURIComponent(JSON.stringify(data))}`;
  }
  return `<p><card name="${name}" value="${value}"></card></p>`;
}

test('Yuque converter collects attachment cards that store the download URL in src/href', () => {
  const cases = [
    ['file', { filename: '合同.doc', src: 'https://cdn.example.test/a.doc', size: 12 }],
    ['file', { filename: '合同.doc', href: 'https://cdn.example.test/b.doc' }],
    ['attachment', { name: '合同.doc', downloadUrl: 'https://cdn.example.test/c.doc' }],
    ['localfile', { name: '合同.doc', download_url: 'https://cdn.example.test/d.doc' }],
  ];

  for (const [name, data] of cases) {
    const result = runConverter(fileCard(name, data));
    const url = data.src || data.href || data.downloadUrl || data.download_url;
    assert.ok(
      result.resources.some((item) => item.url === url && item.kind === 'attachment'),
      `card ${name} should collect ${url}, got ${JSON.stringify(result.resources)}`
    );
    assert.match(result.markdown, new RegExp(`\\]\\(${url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\)`));
  }
});

test('Yuque converter decodes media-type and base64 card payloads', () => {
  const url = 'https://cdn.example.test/e.doc';
  for (const encodeStyle of ['json-prefix', 'base64']) {
    const result = runConverter(fileCard('file', { filename: 'e.doc', url }, encodeStyle));
    assert.ok(
      result.resources.some((item) => item.url === url && item.kind === 'attachment'),
      `encodeStyle ${encodeStyle} should collect the attachment`
    );
    assert.match(result.markdown, new RegExp(`\\]\\(${url}\\)`));
  }
});

test('Yuque converter keeps doc-type file attachments but drops document reference cards', () => {
  const fileResult = runConverter(fileCard('doc', { filename: '需求.doc', src: 'https://cdn.example.test/need.doc', size: 3 }));
  assert.ok(fileResult.resources.some((item) => item.url === 'https://cdn.example.test/need.doc' && item.kind === 'attachment'));
  assert.match(fileResult.markdown, /\[需求\.doc\]\(https:\/\/cdn\.example\.test\/need\.doc\)/);

  const refResult = runConverter(fileCard('doc', { id: 9, slug: 'ref-doc', title: '关联文档', url: 'https://www.yuque.com/team/book/ref-doc' }));
  assert.equal(refResult.resources.filter((item) => item.kind === 'attachment').length, 0);
  assert.match(refResult.markdown, /\[关联文档\]\(https:\/\/www\.yuque\.com\/team\/book\/ref-doc\)/);
});

test('Yuque converter treats links with query strings as attachments and upgrades http URLs', () => {
  const result = runConverter(
    '<p><a href="http://cdn.example.test/guide.doc?token=abc">guide.doc</a></p>'
  );
  assert.ok(result.resources.some((item) => item.url === 'https://cdn.example.test/guide.doc?token=abc' && item.kind === 'attachment'));
  assert.match(result.markdown, /\[guide\.doc\]\(https:\/\/cdn\.example\.test\/guide\.doc\?token=abc\)/);
  assert.ok(result.resources.every((item) => item.url.startsWith('https://')));
});
