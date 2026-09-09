import { Fragment, type ReactNode } from "react";

const BOLD = /\*\*([^*]+)\*\*/;
const CODE = /`([^`]+)`/;
const LINK = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/;

// Model output is untrusted, so every node is built by React and never as raw HTML.
function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let index = 0;
  while (rest.length > 0) {
    const link = LINK.exec(rest);
    const bold = BOLD.exec(rest);
    const code = CODE.exec(rest);
    const next = [link, bold, code]
      .filter((match): match is RegExpExecArray => match !== null)
      .sort((left, right) => left.index - right.index)[0];
    if (!next) {
      nodes.push(rest);
      break;
    }
    if (next.index > 0) nodes.push(rest.slice(0, next.index));
    const key = `${keyPrefix}-${String(index)}`;
    index += 1;
    if (next === link) {
      nodes.push(
        <a href={next[2]} key={key} rel="noopener noreferrer" target="_blank">
          {next[1]}
        </a>,
      );
    } else if (next === bold) {
      nodes.push(<strong key={key}>{next[1]}</strong>);
    } else {
      nodes.push(<code key={key}>{next[1]}</code>);
    }
    rest = rest.slice(next.index + next[0].length);
  }
  return nodes;
}

function isBullet(line: string): boolean {
  return /^\s*[-*]\s+/.test(line);
}

type Alignment = "left" | "center" | "right";

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isDelimiterRow(line: string): boolean {
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

function isTable(lines: string[]): boolean {
  const [header, delimiter] = lines;
  if (header === undefined || delimiter === undefined) return false;
  if (!header.includes("|") || !isDelimiterRow(delimiter)) return false;
  return splitRow(header).length === splitRow(delimiter).length;
}

function splitBlocks(text: string): string[] {
  const lines = text.split("\n");
  return lines
    .flatMap((line, index) =>
      isTable(lines.slice(index, index + 2)) ? ["", line] : [line],
    )
    .join("\n")
    .split(/\n{2,}/)
    .filter((block) => block.trim().length > 0);
}

function alignmentOf(cell: string): Alignment {
  const leading = cell.startsWith(":");
  const trailing = cell.endsWith(":");
  if (leading && trailing) return "center";
  return trailing ? "right" : "left";
}

export function MarkdownText({ text }: { text: string }) {
  const blocks = splitBlocks(text);
  return (
    <>
      {blocks.map((block, blockIndex) => {
        const lines = block
          .split("\n")
          .filter((line) => line.trim().length > 0);
        const key = `block-${String(blockIndex)}`;
        if (isTable(lines)) {
          const headers = splitRow(lines[0] ?? "");
          const align = splitRow(lines[1] ?? "").map(alignmentOf);
          const rows = lines.slice(2).map(splitRow);
          return (
            <table className="markdown-table" key={key}>
              <thead>
                <tr>
                  {headers.map((cell, columnIndex) => {
                    const cellKey = `${key}-h-${String(columnIndex)}`;
                    return (
                      <th
                        key={cellKey}
                        style={{ textAlign: align[columnIndex] ?? "left" }}
                      >
                        {inline(cell, cellKey)}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIndex) => (
                  <tr key={`${key}-r-${String(rowIndex)}`}>
                    {headers.map((_, columnIndex) => {
                      const cellKey = `${key}-r-${String(rowIndex)}-c-${String(columnIndex)}`;
                      return (
                        <td
                          key={cellKey}
                          style={{ textAlign: align[columnIndex] ?? "left" }}
                        >
                          {inline(row[columnIndex] ?? "", cellKey)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          );
        }
        if (lines.length > 0 && lines.every(isBullet)) {
          return (
            <ul className="markdown-list" key={key}>
              {lines.map((line, lineIndex) => (
                <li key={`${key}-${String(lineIndex)}`}>
                  {inline(
                    line.replace(/^\s*[-*]\s+/, ""),
                    `${key}-${String(lineIndex)}`,
                  )}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p className="markdown-paragraph" key={key}>
            {lines.map((line, lineIndex) => (
              <Fragment key={`${key}-${String(lineIndex)}`}>
                {lineIndex > 0 && <br />}
                {inline(line, `${key}-${String(lineIndex)}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}
