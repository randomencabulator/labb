import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

// The app's name is in index.html's <title>, its look on <html> (see themes.css)
const NAME = document.title;

// The items API (backend/items.py): an example to change into what the app is for
export default function App() {
  // null while they load
  const [items, setItems] = useState(null);
  const [failed, setFailed] = useState("");
  const [title, setTitle] = useState("");

  useEffect(() => {
    api("/api/items").then(setItems, error => setFailed(error.message));
  }, []);

  const add = async event => {
    event.preventDefault();
    try {
      const item = await api("/api/items", { method: "POST", body: { title } });
      setItems([...items, item]);
      setTitle("");
    } catch (error) {
      toast.error(error.message);
    }
  };

  const change = async (item, changes) => {
    try {
      const changed = await api(`/api/items/${item.id}`, { method: "PATCH", body: changes });
      setItems(items.map(i => (i.id === item.id ? changed : i)));
    } catch (error) {
      toast.error(error.message);
    }
  };

  const remove = async item => {
    try {
      await api(`/api/items/${item.id}`, { method: "DELETE" });
      setItems(items.filter(i => i.id !== item.id));
      toast(`Removed "${item.title}"`);
    } catch (error) {
      toast.error(error.message);
    }
  };

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-xl flex-col gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl">{NAME}</CardTitle>
          <CardDescription>Items kept by the app's server.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form onSubmit={add} className="flex gap-2">
            <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Something to add" aria-label="New item" />
            <Button type="submit" disabled={!title.trim() || items === null}>
              <Plus /> Add
            </Button>
          </form>
          {failed ? (
            <Alert variant="destructive">
              <AlertTitle>The server didn't answer</AlertTitle>
              <AlertDescription>{failed}</AlertDescription>
            </Alert>
          ) : items === null ? (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-2/3" />
            </div>
          ) : items.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>Nothing yet</EmptyTitle>
                <EmptyDescription>Add the first item above.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <ul className="flex flex-col divide-y">
              {items.map(item => (
                <li key={item.id} className="flex items-center gap-3 py-2">
                  <Checkbox checked={item.done} onCheckedChange={done => change(item, { done: done === true })} aria-label={`Done: ${item.title}`} />
                  <span className={item.done ? "flex-1 text-muted-foreground line-through" : "flex-1"}>{item.title}</span>
                  <Button variant="ghost" size="icon-sm" onClick={() => remove(item)} aria-label={`Remove ${item.title}`}>
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
