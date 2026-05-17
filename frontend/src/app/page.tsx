"use client";

import { useEffect, useRef, useState } from "react";

type Role = "system" | "user" | "assistant";
type ChatMessage = { role: Role; content: string };

type AnimeItem = { name: string; mal_url: string | null };

type Recommendations = {
  most_similar: AnimeItem[];
  by_genre: AnimeItem[];
  hidden_gems: AnimeItem[];
};

type RecommendApiPayload = Recommendations & { assistant_chat?: string };

/** Accept only absolute myanimelist.net anime URLs from the API (no site search). */
function directMalUrl(malUrl: string | null | undefined): string | null {
  let raw = malUrl?.trim();
  if (!raw) return null;
  if (raw.startsWith("//")) raw = `https:${raw}`;
  if (!/^https?:\/\//i.test(raw)) {
    if (/^(www\.)?myanimelist\.net\//i.test(raw)) raw = `https://${raw}`;
    else return null;
  }
  try {
    const u = new URL(raw);
    const host = u.hostname.replace(/^www\./i, "").toLowerCase();
    if (host !== "myanimelist.net") return null;
    u.protocol = "https:";
    return u.href;
  } catch {
    return null;
  }
}

/** Rich context for POST /feedback only (full lists); not shown in the chat panel. */
function formatRecommendationsForChat(recs: Recommendations): string {
  const block = (title: string, items: AnimeItem[] | undefined) =>
    items?.length
      ? `${title}:\n${items.map((i) => `• ${i.name}`).join("\n")}`
      : `${title}: (none)`;

  return [
    "Here are some picks based on what you asked for:",
    block("Most similar", recs.most_similar),
    block("By genre", recs.by_genre),
    block("Hidden gems", recs.hidden_gems),
  ].join("\n\n");
}

function MessageParagraphs({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\n+/);
  return (
    <div className="text-sm leading-relaxed text-zinc-100">
      {blocks.map((block, i) => (
        <p key={i} className="mb-3 whitespace-pre-wrap last:mb-0">
          {block}
        </p>
      ))}
    </div>
  );
}

function Section({
  title,
  items,
  onPick,
  className = "",
  compact = false,
}: {
  title: string;
  items: AnimeItem[] | undefined;
  onPick?: (name: string) => void;
  className?: string;
  compact?: boolean;
}) {
  const pad = compact ? "p-4" : "p-5";
  const titleCls = compact
    ? "text-xl font-semibold tracking-tight text-white"
    : "text-lg font-semibold text-white";
  const listCls = compact
    ? "scrollbar-dark mt-3 min-h-0 flex-1 list-disc space-y-2 overflow-y-auto pl-5 text-base leading-relaxed text-zinc-200"
    : "scrollbar-dark mt-4 min-h-0 flex-1 list-disc space-y-2 overflow-y-auto pl-5 text-zinc-300";

  return (
    <section
      className={`flex min-h-0 flex-col rounded-lg border border-zinc-700/80 bg-zinc-900 ${pad} ${className}`}
    >
      <h2 className={`shrink-0 ${titleCls}`}>{title}</h2>

      {!items?.length ? (
        <p className="mt-3 shrink-0 text-base text-zinc-500">
          No results yet.
        </p>
      ) : (
        <ul className={listCls}>
          {items.map((x, i) => {
            const href = directMalUrl(x.mal_url);
            return (
              <li key={`${x.name}-${i}`}>
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => onPick?.(x.name)}
                    className="text-left underline decoration-zinc-600 underline-offset-2 hover:text-zinc-100 hover:decoration-zinc-400"
                    title="Open on MyAnimeList (new tab)"
                  >
                    {x.name}
                  </a>
                ) : (
                  <span
                    className="text-zinc-400"
                    title="MyAnimeList link unavailable (title could not be matched)"
                  >
                    {x.name}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default function Home() {
  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ??
    "http://127.0.0.1:8000";

  const [recs, setRecs] = useState<Recommendations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recommendLoading, setRecommendLoading] = useState(false);

  /** Full history sent to POST /feedback (includes hidden recommend turn). */
  const [groqHistory, setGroqHistory] = useState<ChatMessage[]>([
    {
      role: "system",
      content:
        "You are an expert anime recommender. The user may ask for different recommendations, tweaks to mood or genre, shorter series, or alternatives to shows they dislike. Be concise and helpful.",
    },
  ]);
  /** Chat panel: first exchange after /recommend + follow-up /feedback turns. */
  const [panelMessages, setPanelMessages] = useState<ChatMessage[]>([]);

  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const [myAnime, setMyAnime] = useState<string[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [panelMessages.length, chatLoading, recommendLoading]);

  const busy = recommendLoading || chatLoading;
  const canSend = chatInput.trim().length > 0 && !busy;

  const addToMyAnime = (name: string) => {
    const normalized = name.trim();
    if (!normalized) return;
    setMyAnime((prev) =>
      prev.some((x) => x.toLowerCase() === normalized.toLowerCase())
        ? prev
        : [normalized, ...prev]
    );
  };

  const runRecommend = async (message: string) => {
    setRecommendLoading(true);
    setError(null);

    try {
      const res = await fetch(`${apiBase}/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `Request failed (${res.status})`);
      }

      const payload = (await res.json()) as RecommendApiPayload;
      const recsPayload: Recommendations = {
        most_similar: payload.most_similar ?? [],
        by_genre: payload.by_genre ?? [],
        hidden_gems: payload.hidden_gems ?? [],
      };
      setRecs(recsPayload);

      const chatReply =
        typeof payload.assistant_chat === "string" &&
          payload.assistant_chat.trim()
          ? payload.assistant_chat.trim()
          : "I've added picks on the right in three columns. Browse there and tell me what you'd like to change.";

      setGroqHistory((h) => [
        ...h,
        { role: "user", content: message },
        { role: "assistant", content: formatRecommendationsForChat(recsPayload) },
      ]);
      setPanelMessages((prev) => [
        ...prev,
        { role: "user", content: message },
        { role: "assistant", content: chatReply },
      ]);
      setChatInput("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setRecommendLoading(false);
    }
  };

  const runFeedback = async (message: string) => {
    setChatLoading(true);
    setChatError(null);

    try {
      const res = await fetch(`${apiBase}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history: groqHistory,
        }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `Request failed (${res.status})`);
      }

      const data = (await res.json()) as {
        response: string;
        history: ChatMessage[];
        recommendations?: Recommendations | null;
      };

      setGroqHistory(data.history);
      if (
        data.recommendations &&
        Array.isArray(data.recommendations.most_similar)
      ) {
        setRecs({
          most_similar: data.recommendations.most_similar,
          by_genre: data.recommendations.by_genre ?? [],
          hidden_gems: data.recommendations.hidden_gems ?? [],
        });
      }
      setPanelMessages((prev) => [
        ...prev,
        { role: "user", content: message },
        { role: "assistant", content: data.response },
      ]);
      setChatInput("");
    } catch (e) {
      setChatError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setChatLoading(false);
    }
  };

  const submitFromChat = async () => {
    const text = chatInput.trim();
    if (!text || busy) return;

    if (recs === null) {
      await runRecommend(text);
    } else {
      await runFeedback(text);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <main className="mx-auto w-full max-w-[1600px] px-4 py-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-4xl font-black tracking-tight text-white">
            Anime{" "}
            <span className="text-purple-400">Recommender</span>
          </h1>
        </header>

        {/* Wider chat (~63%), narrower recommendation stack */}
        <div className="mt-8 grid gap-4 lg:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)] lg:items-stretch lg:gap-5">
          <section className="flex max-h-[min(560px,70vh)] flex-col rounded-lg border border-zinc-700/80 bg-zinc-900 lg:sticky lg:top-6 lg:max-h-[calc(100vh-8rem)]">
            <div className="border-b border-zinc-700/80 px-4 py-3">
              <h2 className="text-lg font-semibold text-white">
                What are you looking for?
              </h2>
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <div className="scrollbar-dark min-h-[140px] flex-1 space-y-3 overflow-y-auto bg-zinc-950/85 p-4">
                {panelMessages.map((m, i) => (
                  <div
                    key={`${i}-${m.role}`}
                    className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={
                        m.role === "user"
                          ? "max-w-[85%] rounded-lg bg-purple-600/85 px-3 py-2 text-sm leading-relaxed text-white"
                          : "max-w-full rounded-lg border border-zinc-700 bg-zinc-800/90 px-3 py-2 text-sm leading-relaxed text-zinc-100"
                      }
                    >
                      {m.role === "assistant" ? (
                        <MessageParagraphs text={m.content} />
                      ) : (
                        <p className="whitespace-pre-wrap">{m.content}</p>
                      )}
                    </div>
                  </div>
                ))}
                {busy ? (
                  <div className="flex justify-start">
                    <div className="rounded-lg border border-zinc-700 bg-zinc-800/90 px-3 py-2 text-sm text-zinc-400">
                      {recommendLoading
                        ? "Finding recommendations…"
                        : "Updating chat & lists…"}
                    </div>
                  </div>
                ) : null}
                <div ref={messagesEndRef} />
              </div>

              <div className="border-t border-zinc-800 bg-zinc-950/70 p-3">
                {error ? (
                  <p className="mb-2 rounded-md border border-red-900/60 bg-red-950/35 px-3 py-2 text-sm text-red-200/90">
                    {error}
                  </p>
                ) : null}
                {chatError ? (
                  <p className="mb-2 rounded-md border border-red-900/60 bg-red-950/35 px-3 py-2 text-sm text-red-200/90">
                    {chatError}
                  </p>
                ) : null}
                <div className="flex items-end gap-2">
                  <textarea
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        if (canSend) void submitFromChat();
                      }
                    }}
                    placeholder='e.g. "something like Death Note—dark and clever"'
                    rows={1}
                    className="scrollbar-dark min-h-12 max-h-40 min-w-0 flex-1 resize-y rounded-md border border-zinc-700 bg-zinc-900/90 px-3 py-2 text-sm leading-snug text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-zinc-500 focus:ring-1 focus:ring-zinc-500/40"
                  />
                  <button
                    type="button"
                    onClick={() => void submitFromChat()}
                    disabled={!canSend}
                    className="inline-flex h-12 shrink-0 items-center justify-center rounded-lg bg-purple-500 px-5 text-sm font-semibold text-white shadow-md shadow-purple-900/20 transition hover:bg-purple-400 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-purple-500"
                  >
                    {recommendLoading
                      ? "…"
                      : chatLoading
                        ? "…"
                        : "Send"}
                  </button>
                </div>
              </div>
            </div>
          </section>

          <div className="flex min-h-[min(520px,70vh)] flex-col gap-2 lg:min-h-[calc(100vh-10rem)] lg:gap-2">
            <Section
              title="Most Similar"
              items={recs?.most_similar}
              onPick={addToMyAnime}
              className="flex-1"
              compact
            />
            <Section
              title="By Genre"
              items={recs?.by_genre}
              onPick={addToMyAnime}
              className="flex-1"
              compact
            />
            <Section
              title="Hidden Gems"
              items={recs?.hidden_gems}
              onPick={addToMyAnime}
              className="flex-1"
              compact
            />
          </div>
        </div>
      </main>
    </div>
  );
}
