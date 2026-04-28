"use client";

import { useMemo, useState } from "react";

type Role = "system" | "user" | "assistant";
type ChatMessage = { role: Role; content: string };

type AnimeItem = { name: string; mal_url: string | null };

type Recommendations = {
  most_similar: AnimeItem[];
  by_genre: AnimeItem[];
  hidden_gems: AnimeItem[];
};

function Section({
  title,
  items,
  onPick,
}: {
  title: string;
  items: AnimeItem[] | undefined;
  onPick?: (name: string) => void;
}) {
  return (
    <section className="rounded-2xl border border-purple-900/50 bg-[#120a1f] p-5">
      <h2 className="text-lg font-semibold text-white">{title}</h2>

      {!items?.length ? (
        <p className="mt-3 text-sm text-purple-200/60">No results yet.</p>
      ) : (
        <ul className="mt-4 list-disc space-y-2 pl-5 text-purple-100">
          {items.map((x) => (
            <li key={x.mal_url ?? x.name}>
              {x.mal_url ? (
                <a
                  href={x.mal_url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => onPick?.(x.name)}
                  className="text-left underline decoration-purple-400/40 underline-offset-4 hover:decoration-purple-200"
                  title="Open on MyAnimeList (also adds to My Anime)"
                >
                  {x.name}
                </a>
              ) : onPick ? (
                <button
                  type="button"
                  onClick={() => onPick(x.name)}
                  className="text-left underline decoration-purple-400/40 underline-offset-4 hover:decoration-purple-200"
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

  const [prompt, setPrompt] = useState("");
  const [recs, setRecs] = useState<Recommendations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [history, setHistory] = useState<ChatMessage[]>([
    {
      role: "system",
      content:
        "You are an expert anime recommender. The user has received recommendations and may want adjustments. Help them find the perfect anime based on their feedback.",
    },
  ]);

  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackReply, setFeedbackReply] = useState<string | null>(null);

  const [myAnime, setMyAnime] = useState<string[]>([]);

  const canRecommend = prompt.trim().length > 0 && !loading;
  const canFeedback = feedbackText.trim().length > 0 && !loading;

  const addToMyAnime = (name: string) => {
    const normalized = name.trim();
    if (!normalized) return;
    setMyAnime((prev) =>
      prev.some((x) => x.toLowerCase() === normalized.toLowerCase())
        ? prev
        : [normalized, ...prev]
    );
  };

  const removeFromMyAnime = (name: string) => {
    setMyAnime((prev) => prev.filter((x) => x !== name));
  };

  const recommend = async () => {
    setLoading(true);
    setError(null);
    setFeedbackReply(null);

    try {
      const res = await fetch(`${apiBase}/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: prompt.trim() }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `Request failed (${res.status})`);
      }

      const data = (await res.json()) as Recommendations;
      setRecs(data);

      setHistory((h) => [
        ...h,
        { role: "user", content: prompt.trim() },
        { role: "assistant", content: JSON.stringify(data) },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const sendFeedback = async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${apiBase}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: feedbackText.trim(),
          history,
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

      setFeedbackReply(data.response);
      setHistory(data.history);
      setFeedbackText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const hint = useMemo(() => {
    if (apiBase.includes("127.0.0.1") || apiBase.includes("localhost")) {
      return `API: ${apiBase} (local)`;
    }

    return `API: ${apiBase}`;
  }, [apiBase]);

  return (
    <div className="min-h-screen bg-[#05000e] text-white">
      <main className="mx-auto w-full max-w-5xl px-4 py-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-4xl font-black tracking-tight text-white">
            Anime{" "}
            <span className="text-purple-400">
              Recommender
            </span>
          </h1>

          <p className="text-sm text-purple-200/70">{hint}</p>
        </header>

        <section className="mt-8 rounded-2xl border border-purple-800/50 bg-[#120a1f]/90 p-5 shadow-2xl">
          <label className="text-sm font-medium text-purple-100">
            What are you looking for?
          </label>

          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder='e.g. "something like Death Note, dark and smart"'
              className="h-12 w-full rounded-xl border border-purple-800/70 bg-[#1a102b] px-4 text-white outline-none placeholder:text-purple-300/40 focus:border-purple-400 focus:ring-2 focus:ring-purple-500/30"
            />

            <button
              onClick={recommend}
              disabled={!canRecommend}
              className="h-12 rounded-xl bg-purple-500 px-6 font-semibold text-white shadow-lg shadow-purple-500/30 transition hover:scale-[1.02] hover:from-purple-500 hover:to-pink-400 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100"
            >
              {loading ? "Working..." : "Recommend"}
            </button>
          </div>

          {error ? (
            <p className="mt-3 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-sm text-red-200">
              {error}
            </p>
          ) : null}
        </section>

        <section className="mt-6 grid gap-4 md:grid-cols-3">
          <Section
            title="Most Similar"
            items={recs?.most_similar}
            onPick={addToMyAnime}
          />
          <Section title="By Genre" items={recs?.by_genre} onPick={addToMyAnime} />
          <Section
            title="Hidden Gems"
            items={recs?.hidden_gems}
            onPick={addToMyAnime}
          />
        </section>

        <section className="mt-8 rounded-2xl border border-purple-800/50 bg-[#120a1f] p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-white">
              Refine with feedback
            </h2>

            <p className="text-xs text-purple-200/60">
              Uses{" "}
              <code className="rounded bg-purple-950/70 px-1.5 py-0.5 text-pink-300">
                /feedback
              </code>
            </p>
          </div>

          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              value={feedbackText}
              onChange={(e) => setFeedbackText(e.target.value)}
              placeholder='e.g. "less gore, more mystery, shorter shows"'
              className="h-12 w-full rounded-xl border border-purple-800/70 bg-[#1a102b] px-4 text-white outline-none placeholder:text-purple-300/40 focus:border-purple-400 focus:ring-2 focus:ring-purple-500/30"
            />

            <button
              onClick={sendFeedback}
              disabled={!canFeedback}
              className="h-12 rounded-xl border border-purple-500/40 bg-purple-500/15 px-6 font-semibold text-purple-100 shadow-lg shadow-purple-500/10 transition hover:bg-purple-500/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Working..." : "Send"}
            </button>
          </div>

          {feedbackReply ? (
            <div className="mt-4 rounded-xl border border-purple-800/50 bg-[#1a102b] p-4 text-sm text-purple-100">
              {feedbackReply}
            </div>
          ) : (
            <p className="mt-3 text-sm text-purple-200/60">
              After you get recommendations, use this to ask for tweaks.
            </p>
          )}
        </section>
      </main>
    </div>
  );
}