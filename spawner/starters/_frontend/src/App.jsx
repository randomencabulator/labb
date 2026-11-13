import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

// The app's name is in index.html's <title>, its look on <html> (see themes.css)
const NAME = document.title;
const LOOK = document.documentElement.dataset;

export default function App() {
  const [clicks, setClicks] = useState(0);
  const click = () => {
    setClicks(clicks + 1);
    toast(`Clicked ${clicks + 1} time${clicks === 0 ? "" : "s"}`);
  };
  return (
    <main className="grid min-h-svh place-items-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-2xl">{NAME}</CardTitle>
          <CardDescription>
            A React app with shadcn/ui: edit <code className="font-mono">frontend/src/App.jsx</code>, then rebuild.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Badge>{LOOK.color}</Badge>
          <Badge variant="secondary">{LOOK.style}</Badge>
          <Badge variant="outline">{LOOK.font}</Badge>
        </CardContent>
        <CardFooter>
          <Button onClick={click}>Clicked {clicks} time{clicks === 1 ? "" : "s"}</Button>
        </CardFooter>
      </Card>
    </main>
  );
}
