# Architecture

## Directory structure
```
src/
├── app/               # pages + API routes
├── components/        # UI components
├── types/             # TypeScript type definitions
├── lib/               # utilities + helpers
└── services/          # external API wrappers
```

## Patterns
{design patterns in use (e.g. Server Components by default, Client Components only where interaction is needed)}

## Data flow
```
{how data flows (e.g.
user input → Client Component → API Route → external API → response → UI update
)}
```

## State management
{how state is managed (e.g. server state in Server Components, client state in useState/useReducer)}
