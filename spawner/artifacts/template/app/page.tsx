"use client";

// The page. Replace what's here with what was asked for; examples/ has a dashboard, diagrams and a
// form to copy from.

import { SparkleIcon } from "@phosphor-icons/react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui";

export default function Page() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col justify-center gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <SparkleIcon className="size-5 text-primary" />
            A new page
          </CardTitle>
          <CardDescription>Nothing here yet.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">It's being made.</CardContent>
      </Card>
    </main>
  );
}
