import React from 'react';

interface MarkdownRendererProps {
    content: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => {
    const parseMarkdown = (text: string): React.ReactNode => {
        const lines = text.split('\n');
        return lines.map((line, index) => {
            if (line.startsWith('# ')) {
                return <h1 key={index}>{line.slice(2)}</h1>;
            } else if (line.startsWith('## ')) {
                return <h2 key={index}>{line.slice(3)}</h2>;
            } else if (line.startsWith('### ')) {
                return <h3 key={index}>{line.slice(4)}</h3>;
            } else if (line.startsWith('#### ')) {
                return <h4 key={index}>{line.slice(5)}</h4>;
            } else if (line.startsWith('##### ')) {
                return <h5 key={index}>{line.slice(6)}</h5>;
            } else if (line.startsWith('###### ')) {
                return <h6 key={index}>{line.slice(7)}</h6>;
            } else if (line.startsWith('- ') || line.startsWith('* ')) {
                return <li key={index}>{line.slice(2)}</li>;
            } else if (line.trim() === '') {
                return <br key={index} />;
            } else {
                return <p key={index}>{line}</p>;
            }
        });
    };

    return (
        <div className="prose prose-gray dark:prose-invert max-w-none">
            {parseMarkdown(content)}
        </div>
    );
};