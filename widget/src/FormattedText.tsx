import { Fragment, type ReactNode } from "react";

/**
 * Renders model output as React elements: paragraphs, bullet/numbered lists,
 * **bold**, [text](url), bare URLs and emails become links. No HTML is ever
 * injected, so the model cannot add markup or scripts.
 */

// 1: md link text, 2: md link url, 3: bold, 4: bare url, 5: email
const INLINE =
  /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*|(https?:\/\/[^\s<]+)|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)/g;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^#{1,6}\s+(.*)$/;

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    const key = `${start}`;
    if (match[2]) {
      nodes.push(
        <Link key={key} href={match[2]}>
          {match[1]}
        </Link>,
      );
    } else if (match[3]) {
      nodes.push(<strong key={key}>{match[3]}</strong>);
    } else if (match[4]) {
      // Keep sentence punctuation such as "." or ")" outside the link.
      const trailing = match[4].match(/[.,;:!?)]+$/)?.[0] ?? "";
      const url = trailing ? match[4].slice(0, -trailing.length) : match[4];
      nodes.push(
        <Link key={key} href={url}>
          {url}
        </Link>,
      );
      if (trailing) nodes.push(trailing);
    } else if (match[5]) {
      nodes.push(
        <Link key={key} href={`mailto:${match[5]}`}>
          {match[5]}
        </Link>,
      );
    }
    last = start + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      blocks.push({ kind: "p", text: "" }); // paragraph break marker
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    const kind = bullet ? "ul" : numbered ? "ol" : null;
    const previous = blocks[blocks.length - 1];
    if (kind) {
      const item = (bullet ?? numbered)![1];
      if (previous && previous.kind === kind) previous.items.push(item);
      else blocks.push({ kind, items: [item] });
      continue;
    }
    const heading = HEADING.exec(line);
    const content = heading ? `**${heading[1].replace(/\*\*/g, "")}**` : line;
    if (previous && previous.kind === "p" && previous.text) {
      previous.text += `\n${content}`;
    } else {
      blocks.push({ kind: "p", text: content });
    }
  }
  return blocks.filter((b) => b.kind !== "p" || b.text);
}

export function FormattedText({ text }: { text: string }) {
  return (
    <>
      {parseBlocks(text).map((block, index) => {
        if (block.kind === "p") {
          return (
            <p key={index}>
              {block.text.split("\n").map((line, i) => (
                <Fragment key={i}>
                  {i > 0 && <br />}
                  {renderInline(line)}
                </Fragment>
              ))}
            </p>
          );
        }
        const Tag = block.kind;
        return (
          <Tag key={index}>
            {block.items.map((item, i) => (
              <li key={i}>{renderInline(item)}</li>
            ))}
          </Tag>
        );
      })}
    </>
  );
}
