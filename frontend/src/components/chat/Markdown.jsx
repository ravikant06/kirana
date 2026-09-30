// A small Markdown renderer for assistant replies: paragraphs, bullet and numbered
// lists, headings, **bold**, *italic* and `code`. That is all the model writes here.
//
// It builds React elements and never uses dangerouslySetInnerHTML, so text from the
// model (which can echo text from documents, and later from users) is always shown
// as text, never run as markup. That matters from Phase 8 on: prompt injection can
// make a model emit <img onerror=...>, and this renderer would print it literally.

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g

function inline(text, keyBase) {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const key = `${keyBase}-${i}`
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={key}>{part.slice(1, -1)}</code>
    if ((part.startsWith('*') && part.endsWith('*')) || (part.startsWith('_') && part.endsWith('_')))
      return <em key={key}>{part.slice(1, -1)}</em>
    return part
  })
}

const BULLET = /^\s*[-*•]\s+/
const NUMBER = /^\s*\d+[.)]\s+/
const HEADING = /^\s*#{1,4}\s+/

// Split into blocks: runs of list items stay together, everything else splits on blank lines.
function blocks(text) {
  const out = []
  let current = null
  for (const line of text.split('\n')) {
    const kind = BULLET.test(line) ? 'ul' : NUMBER.test(line) ? 'ol' : HEADING.test(line) ? 'h' : line.trim() ? 'p' : null
    if (kind === null) {
      current = null
      continue
    }
    if (kind === 'h') {
      out.push({ kind, lines: [line.replace(HEADING, '')] })
      current = null
    } else if (current && current.kind === kind) {
      current.lines.push(kind === 'p' ? line : line.replace(kind === 'ul' ? BULLET : NUMBER, ''))
    } else {
      current = { kind, lines: [kind === 'p' ? line : line.replace(kind === 'ul' ? BULLET : NUMBER, '')] }
      out.push(current)
    }
  }
  return out
}

export default function Markdown({ text }) {
  return (
    <div className="md">
      {blocks(text).map((b, i) => {
        if (b.kind === 'h') return <p key={i} className="md-h">{inline(b.lines[0], i)}</p>
        if (b.kind === 'p') return <p key={i}>{b.lines.map((l, j) => [j > 0 && <br key={`br${j}`} />, inline(l, `${i}-${j}`)])}</p>
        const List = b.kind
        return (
          <List key={i}>
            {b.lines.map((l, j) => <li key={j}>{inline(l, `${i}-${j}`)}</li>)}
          </List>
        )
      })}
    </div>
  )
}

// The model ends answers with "Sources: policy-returns.md". The UI shows sources as
// chips from the citations the API returns, so drop that line from the text.
export function withoutSourcesLine(text) {
  return text.replace(/\n*\s*\**sources?\**\s*:[^\n]*\s*$/i, '').trimEnd()
}
