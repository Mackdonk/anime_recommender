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

function Section({
  title,
  items,
  onPick,
  className = "",
}: {
  title: string;
  items: AnimeItem[] | undefined;
  onPick?: (name: string) => void;
  className?: string;
}) {
  return (
    <section
      className={`flex min-h-0 flex-col rounded-lg border border-zinc-700/80 bg-zinc-900 p-5 ${className}`}
    >
      <h2 className="shrink-0 text-lg font-semibold text-white">{title}</h2>

      {!items?.length ? (
        <p className="mt-3 shrink-0 text-sm text-zinc-500">No results yet.</p>
      ) : (
        <ul className="mt-4 min-h-0 flex-1 list-disc space-y-2 overflow-y-auto pl-5 text-zinc-300">
          {items.map((x) => (
            <li key={x.mal_url ?? x.name}>
              {x.mal_url ? (
                <a
                  href={x.mal_url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => onPick?.(x.name)}
                  className="text-left underline decoration-zinc-600 underline-offset-2 hover:text-zinc-100 hover:decoration-zinc-400"
                  title="Open on MyAnimeList (also adds to My Anime)"
                >
                  {x.name}
                </a>
              ) : onPick ? (
                <button
                  type="button"
                  onClick={() => onPick(x.name)}
                  className="text-left underline decoration-zinc-600 underline-offset-2 hover:text-zinc-100 hover:decoration-zinc-400"
                  title="Add to My Anime"
                >
                  {x.name}
                </button>
              ) : (
                <span>{x.name}</span>
              )}
            </li>
          ))}
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

  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([
    {
      role: "system",
      content:
        "You are an expert anime recommender. The user may ask for different recommendations, tweaks to mood or genre, shorter series, or alternatives to shows they dislike. Be concise and helpful.",
    },
  ]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const [myAnime, setMyAnime] = useState<string[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const visibleMessages = chatHistory.filter((m) => m.role !== "system");

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleMessages.length, chatLoading, recommendLoading]);

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

      const data = (await res.json()) as Recommendations;
      setRecs(data);

      setChatHistory((h) => [
        ...h,
        { role: "user", content: message },
        { role: "assistant", content: formatRecommendationsForChat(data) },
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
          history: chatHistory,
        }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `Request failed (${res.status})`);
      }

      const data = (await res.json()) as {
        response: string;
        history: ChatMessage[];
      };

      setChatHistory(data.history);
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

        <div className="mt-8 grid gap-6 lg:grid-cols-2 lg:items-stretch">
          {/* Left: single chat = “what are you looking for” + follow-up */}
          <section className="flex max-h-[min(560px,70vh)] flex-col rounded-lg border border-zinc-700/80 bg-zinc-900 lg:sticky lg:top-6 lg:max-h-[calc(100vh-8rem)]">
            <div className="border-b border-zinc-700/80 px-4 py-3">
              <h2 className="text-lg font-semibold text-white">
                What are you looking for?
              </h2>
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-[140px] flex-1 space-y-3 overflow-y-auto p-4">
                {visibleMessages.length > 0
                  ? visibleMessages.map((m, i) => (
                    <div
                      key={`${i}-${m.role}`}
                      className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${m.role === "user"
                          ? "max-w-[85%] bg-purple-600/85 text-white"
                          : "max-w-full border border-zinc-700 bg-zinc-800/90 text-zinc-200"
                          }`}
                      >
                        {m.content}
                      </div>
                    </div>
                  ))
                  : null}
                {busy ? (
                  <div className="flex justify-start">
                    <div className="rounded-lg border border-zinc-700 bg-zinc-800/90 px-3 py-2 text-sm text-zinc-400">
                      {recommendLoading
                        ? "Finding recommendations…"
                        : "Thinking…"}
                    </div>
                  </div>
                ) : null}
                <div ref={messagesEndRef} />
              </div>

              <div className="border-t border-zinc-700/80 p-3">
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
                <div className="flex flex-col gap-2">
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
                    rows={3}
                    className="min-h-[4.5rem] w-full resize-y rounded-md border border-zinc-700 bg-zinc-800/80 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-zinc-500 focus:ring-1 focus:ring-zinc-500/40"
                  />
                  <button
                    type="button"
                    onClick={() => void submitFromChat()}
                    disabled={!canSend}
                    className="h-12 w-full rounded-xl bg-purple-500 px-6 font-semibold text-white shadow-lg shadow-purple-500/30 transition hover:scale-[1.02] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100"
                  >
                    {recommendLoading
                      ? "Working…"
                      : chatLoading
                        ? "Sending…"
                        : "Send"}
                  </button>
                </div>
              </div>
            </div>
          </section>

          {/* Right: stacked lists, fills remaining width & viewport height */}
          <div className="flex min-h-[min(520px,70vh)] flex-col gap-4 lg:min-h-[calc(100vh-10rem)]">
            <Section
              title="Most Similar"
              items={recs?.most_similar}
              onPick={addToMyAnime}
              className="flex-1"
            />
            <Section
              title="By Genre"
              items={recs?.by_genre}
              onPick={addToMyAnime}
              className="flex-1"
            />
            <Section
              title="Hidden Gems"
              items={recs?.hidden_gems}
              onPick={addToMyAnime}
              className="flex-1"
            />
          </div>
        </div>
      </main>
    </div>
  );
}
