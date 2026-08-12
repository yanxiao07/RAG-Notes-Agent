import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownContentProps = {
  className?: string;
  content: string;
};

const SAFE_LINK_PROTOCOL = /^(https?:|mailto:)/i;

/**
 * 修复部分 PDF/OCR 解析器丢失 Markdown 表头分隔行的情况。
 * 仅处理连续的管道行，正常 GFM 表格和普通段落不会被改写。
 */
export function normalizeMarkdown(content: string) {
  const sourceLines = content
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000\u0001]/g, "")
    .replace(/\u00a0/g, " ")
    .split("\n");
  // 先保留明确的管道单元格边界，再处理 PDF 用空格对齐的无管道表格。
  // 这两个修复只能作用于围栏外，避免代码里的 | 或多空格被误判为表格。
  const lines = restoreTablesOutsideFences(sourceLines);
  const normalized: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (isCodeBlockMarker(line)) {
      const codeLines = collectNumberedCode(lines, index + 1);
      if (codeLines.length >= 2) {
        normalized.push("```python", ...codeLines, "```");
        index += codeLines.length;
        continue;
      }
    }
    // 长文档切块后，后续代码片段可能丢失“代码块”标题，仍可根据连续行号可靠识别。
    const inferredCodeLines = collectNumberedCode(lines, index);
    if (inferredCodeLines.length >= 2) {
      normalized.push("```python", ...inferredCodeLines, "```");
      index += inferredCodeLines.length - 1;
      continue;
    }
    normalized.push(line);
  }
  return normalized.join("\n");
}

function restoreTablesOutsideFences(lines: string[]) {
  const restored: string[] = [];
  let prose: string[] = [];
  let fence: string | null = null;

  const flushProse = () => {
    if (prose.length === 0) return;
    restored.push(...restoreWhitespaceTables(restorePipeTables(prose)));
    prose = [];
  };

  for (const line of lines) {
    const marker = line.trim().match(/^(`{3,}|~{3,})/);
    if (fence === null && marker) {
      flushProse();
      fence = marker[1];
      restored.push(line);
      continue;
    }
    if (fence !== null) {
      restored.push(line);
      if (line.trim().startsWith(fence)) fence = null;
      continue;
    }
    prose.push(line);
  }
  flushProse();
  return restored;
}

function isTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function buildTableSeparator(header: string) {
  const columns = header.split("|").filter((cell, index, cells) => {
    return cell.trim() || (index > 0 && index < cells.length - 1);
  });
  return `|${columns.map(() => " --- ").join("|")}|`;
}

function restorePipeTables(lines: string[]) {
  const restored: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const firstCells = pipeCells(lines[index]);
    if (firstCells.length < 2 || isTableSeparator(lines[index])) {
      restored.push(lines[index]);
      continue;
    }
    const rows: string[][] = [];
    let cursor = index;
    while (cursor < lines.length) {
      const cells = pipeCells(lines[cursor]);
      if (cells.length < 2) break;
      rows.push(cells);
      cursor += 1;
    }
    if (lines.slice(index, cursor).some(isTableSeparator)) {
      restored.push(...lines.slice(index, cursor));
      index = cursor - 1;
      continue;
    }
    const columnCount = Math.max(...rows.map((row) => row.length));
    const completeRows = rows.filter((row) => row.length === columnCount);
    if (completeRows.length < 2) {
      restored.push(lines[index]);
      continue;
    }
    const firstLineIsFragment =
      !lines[index].trim().startsWith("|") && firstCells.length < columnCount;
    if (firstLineIsFragment) restored.push(lines[index], "");
    const tableRows = firstLineIsFragment ? completeRows : rows;
    const headers = firstLineIsFragment
      ? genericTableHeaders(columnCount)
      : normalizeTableRow(tableRows.shift() ?? [], columnCount);
    restored.push(
      toMarkdownTableRow(headers),
      buildTableSeparator(toMarkdownTableRow(headers)),
    );
    for (const row of tableRows) {
      if (row.length === columnCount) restored.push(toMarkdownTableRow(row));
    }
    index = cursor - 1;
  }
  return restored;
}

function pipeCells(line: string) {
  if ((line.match(/\|/g)?.length ?? 0) < 2) return [];
  return line
    .split("|")
    .map((cell) => cell.trim())
    .filter(Boolean);
}

function genericTableHeaders(columnCount: number) {
  const defaults = ["参数", "类型", "说明", "示例", "补充"];
  return Array.from(
    { length: columnCount },
    (_, index) => defaults[index] ?? `字段 ${index + 1}`,
  );
}

function restoreWhitespaceTables(lines: string[]) {
  const restored: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const header = splitWhitespaceColumns(lines[index]);
    const firstRow = splitWhitespaceColumns(lines[index + 1] ?? "");
    if (!isWhitespaceTableHeader(header) || firstRow.length < header.length) {
      restored.push(lines[index]);
      continue;
    }
    restored.push(
      toMarkdownTableRow(header),
      buildTableSeparator(toMarkdownTableRow(header)),
    );
    index += 1;
    while (index < lines.length) {
      const row = splitWhitespaceColumns(lines[index]);
      if (row.length < header.length) break;
      restored.push(toMarkdownTableRow(normalizeTableRow(row, header.length)));
      index += 1;
    }
    index -= 1;
  }
  return restored;
}

function splitWhitespaceColumns(line: string) {
  return line
    .trim()
    .split(/\s{2,}/)
    .map((cell) => cell.trim())
    .filter(Boolean);
}

function isWhitespaceTableHeader(columns: string[]) {
  return (
    columns.length >= 3 &&
    columns.every((column) => column.length <= 32) &&
    !columns.some((column) => /[{}()[\]=]/.test(column))
  );
}

function normalizeTableRow(columns: string[], expectedColumnCount: number) {
  return [
    ...columns.slice(0, expectedColumnCount - 1),
    columns.slice(expectedColumnCount - 1).join(" "),
  ];
}

function toMarkdownTableRow(columns: string[]) {
  return `| ${columns.join(" | ")} |`;
}

function isCodeBlockMarker(line: string) {
  return /^\s*(?:代码块|code block)\s*$/i.test(line);
}

function collectNumberedCode(lines: string[], start: number) {
  const codeLines: string[] = [];
  let sawCodeSyntax = false;
  for (let index = start; index < lines.length; index += 1) {
    const line = lines[index];
    const match = line.match(/^\s*\d+\s{1,}(.*)$/);
    if (!match) {
      if (!line.trim() && codeLines.length > 0) {
        codeLines.push("");
        continue;
      }
      if (/^\s{4,}\S/.test(line) && codeLines.length > 0) {
        codeLines.push(line.trimStart());
        continue;
      }
      break;
    }
    const code = match[1].replace(/^\s/, "");
    sawCodeSyntax ||= /(?:^\s*(?:from|import|def|class|@|#)|[=(){}\[\]:])/.test(
      code,
    );
    codeLines.push(code);
  }
  return sawCodeSyntax ? codeLines : [];
}

/**
 * 统一渲染受控的 Markdown。react-markdown 默认将原始 HTML 作为文本处理，
 * 因此知识库内容或模型回复不能通过 HTML 注入页面。
 */
export function MarkdownContent({ className, content }: MarkdownContentProps) {
  return (
    <div
      className={
        className ? `markdown-content ${className}` : "markdown-content"
      }
    >
      <ReactMarkdown
        components={{
          a: ({ children, href }) =>
            SAFE_LINK_PROTOCOL.test(href ?? "") ? (
              <a href={href} rel="noreferrer" target="_blank">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt, src }) =>
            src && SAFE_LINK_PROTOCOL.test(src) ? (
              <img alt={alt ?? ""} loading="lazy" src={src} />
            ) : null,
          input: ({ checked, type }) =>
            type === "checkbox" ? (
              <input checked={checked} disabled type="checkbox" />
            ) : null,
          table: ({ children }) => (
            <div className="markdown-table-scroll">
              <table>{children}</table>
            </div>
          ),
        }}
        remarkPlugins={[remarkGfm]}
      >
        {normalizeMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}

/** 列表行只展示摘要，移除 Markdown 标记以保留紧凑、可点击的行结构。 */
export function markdownToPlainText(content: string) {
  return content
    .replace(/!?(\[[^\]]*\])\([^)]*\)/g, "$1")
    .replace(/[`*_~>#]/g, "")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
