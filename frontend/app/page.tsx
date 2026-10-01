"use client";

import { ChangeEvent, FormEvent, useState } from "react";

type Flashcard = {
  question: string;
  answer: string;
};

type QuizQuestion = {
  question: string;
  options: string[];
  correct_answer: string;
  explanation: string;
};

type StudyResult = {
  title: string;
  summary: string;
  key_points: string[];
  flashcards: Flashcard[];
  quiz: QuizQuestion[];
  study_plan: string[];
};

export default function Home() {
  const [content, setContent] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const [result, setResult] = useState<StudyResult | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // --------------------------------
  // File upload
  // --------------------------------

  const handleFileChange = (
    event: ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];

    if (!file) {
      return;
    }

    const allowedExtensions = [
      ".pdf",
      ".docx",
      ".txt",
    ];

    const extension =
      "." + file.name.split(".").pop()?.toLowerCase();

    if (!allowedExtensions.includes(extension)) {
      setError(
        "Chỉ hỗ trợ file PDF, DOCX hoặc TXT."
      );

      event.target.value = "";
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      setError("File không được vượt quá 10MB.");

      event.target.value = "";
      return;
    }

    setSelectedFile(file);
    setError("");
  };

  // --------------------------------
  // Remove selected file
  // --------------------------------

  const handleRemoveFile = () => {
    setSelectedFile(null);

    const input = document.getElementById(
      "study-file"
    ) as HTMLInputElement | null;

    if (input) {
      input.value = "";
    }
  };

  // --------------------------------
  // Generate study material
  // --------------------------------

  const handleSubmit = async (
    event: FormEvent<HTMLFormElement>
  ) => {
    event.preventDefault();

    if (!content.trim() && !selectedFile) {
      setError(
        "Vui lòng nhập nội dung hoặc upload tài liệu."
      );
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const formData = new FormData();

      if (content.trim()) {
        formData.append(
          "content",
          content.trim()
        );
      }

      if (selectedFile) {
        formData.append(
          "file",
          selectedFile
        );
      }

      const response = await fetch(
        "http://127.0.0.1:8000/api/study/generate",
        {
          method: "POST",
          body: formData,
        }
      );

      if (!response.ok) {
        let message =
          "Không thể tạo tài liệu học tập.";

        try {
          const data = await response.json();

          if (data.detail) {
            message = data.detail;
          }
        } catch {
          // Ignore JSON parsing errors.
        }

        throw new Error(message);
      }

      const data: StudyResult =
        await response.json();

      setResult(data);

      setTimeout(() => {
        document
          .getElementById("study-result")
          ?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
      }, 100);
    } catch (error) {
      console.error(error);

      if (error instanceof Error) {
        setError(error.message);
      } else {
        setError(
          "Đã xảy ra lỗi không xác định."
        );
      }
    } finally {
      setLoading(false);
    }
  };

  // --------------------------------
  // Clear
  // --------------------------------

  const handleClear = () => {
    setContent("");
    setSelectedFile(null);
    setResult(null);
    setError("");

    const input = document.getElementById(
      "study-file"
    ) as HTMLInputElement | null;

    if (input) {
      input.value = "";
    }
  };

  return (
    <main className="app">

      <div className="background-glow background-glow-one" />
      <div className="background-glow background-glow-two" />

      <div className="container">

        {/* Header */}

        <header className="topbar">

          <div className="brand">

            <div className="brand-mark">
              SM
            </div>

            <div>
              <h1>StudyMate</h1>

              <span>
                AI Learning Assistant
              </span>
            </div>

          </div>

          <div className="status">

            <span className="status-dot" />

            Gemini AI

          </div>

        </header>


        {/* Hero */}

        <section className="hero">

          <div className="hero-badge">
            AI-powered learning
          </div>

          <h2>
            Turn your study material
            <br />
            into a{" "}
            <span>
              smarter study plan.
            </span>
          </h2>

          <p>
            Upload your document or paste
            your lesson, notes or study
            material. StudyMate will transform
            it into summaries, key points,
            flashcards, quizzes and a study plan.
          </p>

        </section>


        {/* Input */}

        <section className="input-card">

          <div className="section-heading">

            <div>

              <span className="section-number">
                01
              </span>

              <div>

                <h3>
                  Your study material
                </h3>

                <p>
                  Upload a document or paste
                  the content you want to learn.
                </p>

              </div>

            </div>

            {(content || selectedFile) && (
              <button
                type="button"
                className="clear-button"
                onClick={handleClear}
              >
                Clear
              </button>
            )}

          </div>


          <form onSubmit={handleSubmit}>

            {/* File upload */}

            <div className="file-upload">

              <label
                htmlFor="study-file"
                className="file-upload-button"
              >

                <span className="file-upload-icon">
                  +
                </span>

                <span>
                  Upload document
                </span>

              </label>

              <input
                id="study-file"
                type="file"
                accept=".pdf,.docx,.txt"
                onChange={handleFileChange}
                hidden
              />

              <span className="file-upload-hint">
                PDF, DOCX or TXT · Max 10MB
              </span>

              {selectedFile && (
                <div className="selected-file">

                  <div>

                    <strong>
                      {selectedFile.name}
                    </strong>

                    <span>
                      {(
                        selectedFile.size /
                        1024
                      ).toFixed(1)}{" "}
                      KB
                    </span>

                  </div>

                  <button
                    type="button"
                    onClick={handleRemoveFile}
                  >
                    Remove
                  </button>

                </div>
              )}

            </div>


            {/* Divider */}

            <div className="input-divider">
              <span>OR</span>
            </div>


            {/* Text input */}

            <div className="textarea-wrapper">

              <textarea
                value={content}
                onChange={(event) =>
                  setContent(
                    event.target.value
                  )
                }
                placeholder={
                  "Paste your lesson, lecture notes, textbook content...\n\nVí dụ:\nTCP là một giao thức hướng kết nối trong bộ giao thức TCP/IP..."
                }
                rows={12}
              />

              <div className="character-count">
                {content.length.toLocaleString()}{" "}
                characters
              </div>

            </div>


            {/* Error */}

            {error && (
              <div className="error-box">

                <strong>
                  Error
                </strong>

                <span>
                  {error}
                </span>

              </div>
            )}


            {/* Footer */}

            <div className="form-footer">

              <div className="input-hint">

                <span>
                  AI
                </span>

                Summary · Flashcards · Quiz · Study Plan

              </div>

              <button
                type="submit"
                className="generate-button"
                disabled={loading}
              >

                {loading ? (
                  <>
                    <span className="spinner" />

                    Analyzing...
                  </>
                ) : (
                  <>
                    Generate

                    <span className="arrow">
                      →
                    </span>
                  </>
                )}

              </button>

            </div>

          </form>

        </section>


        {/* Loading */}

        {loading && (
          <section className="loading-card">

            <div className="loading-animation">

              <span />
              <span />
              <span />

            </div>

            <h3>
              StudyMate is analyzing your material
            </h3>

            <p>
              Extracting content and creating
              your personalized study material...
            </p>

          </section>
        )}


        {/* Result */}

        {result && !loading && (
          <section
            id="study-result"
            className="result-section"
          >

            {/* Result header */}

            <div className="result-header">

              <div>

                <div className="result-label">
                  GENERATED STUDY MATERIAL
                </div>

                <h2>
                  {result.title}
                </h2>

                <p>
                  Your learning material has
                  been generated by StudyMate.
                </p>

              </div>

              <div className="result-count">

                <strong>
                  {result.quiz.length}
                </strong>

                <span>
                  quiz questions
                </span>

              </div>

            </div>


            {/* Summary */}

            <section className="result-card summary-card">

              <div className="card-title">

                <div className="card-icon">
                  S
                </div>

                <div>

                  <span>
                    01
                  </span>

                  <h3>
                    Summary
                  </h3>

                </div>

              </div>

              <p className="summary-text">
                {result.summary}
              </p>

            </section>


            {/* Key points */}

            <section className="result-card">

              <div className="card-title">

                <div className="card-icon">
                  K
                </div>

                <div>

                  <span>
                    02
                  </span>

                  <h3>
                    Key Points
                  </h3>

                </div>

              </div>

              <div className="key-points">

                {result.key_points.map(
                  (point, index) => (
                    <div
                      className="key-point"
                      key={index}
                    >

                      <span className="point-number">

                        {String(
                          index + 1
                        ).padStart(2, "0")}

                      </span>

                      <p>
                        {point}
                      </p>

                    </div>
                  )
                )}

              </div>

            </section>


            {/* Flashcards */}

            <section className="result-card">

              <div className="card-title">

                <div className="card-icon">
                  F
                </div>

                <div>

                  <span>
                    03
                  </span>

                  <h3>
                    Flashcards
                  </h3>

                </div>

              </div>

              <p className="card-description">
                Click a card to reveal the answer.
              </p>

              <div className="flashcard-grid">

                {result.flashcards.map(
                  (card, index) => (
                    <Flashcard
                      key={index}
                      card={card}
                      index={index}
                    />
                  )
                )}

              </div>

            </section>


            {/* Quiz */}

            <section className="result-card">

              <div className="card-title">

                <div className="card-icon">
                  Q
                </div>

                <div>

                  <span>
                    04
                  </span>

                  <h3>
                    Quiz
                  </h3>

                </div>

              </div>

              <p className="card-description">
                Test your understanding.
              </p>

              <div className="quiz-list">

                {result.quiz.map(
                  (quiz, index) => (
                    <QuizCard
                      key={index}
                      quiz={quiz}
                      index={index}
                    />
                  )
                )}

              </div>

            </section>


            {/* Study plan */}

            <section className="result-card">

              <div className="card-title">

                <div className="card-icon">
                  P
                </div>

                <div>

                  <span>
                    05
                  </span>

                  <h3>
                    Study Plan
                  </h3>

                </div>

              </div>

              <div className="study-plan">

                {result.study_plan.map(
                  (step, index) => (
                    <div
                      className="plan-step"
                      key={index}
                    >

                      <div className="plan-number">
                        {index + 1}
                      </div>

                      <p>
                        {step}
                      </p>

                    </div>
                  )
                )}

              </div>

            </section>


            {/* Bottom */}

            <div className="result-footer">

              <p>
                Generated with AI StudyMate
              </p>

              <button
                type="button"
                onClick={() =>
                  window.scrollTo({
                    top: 0,
                    behavior: "smooth",
                  })
                }
              >
                Create another study set ↑
              </button>

            </div>

          </section>
        )}

      </div>

    </main>
  );
}


/* -------------------------------- */
/* Flashcard */
/* -------------------------------- */

function Flashcard({
  card,
  index,
}: {
  card: Flashcard;
  index: number;
}) {
  const [showAnswer, setShowAnswer] =
    useState(false);

  return (
    <button
      type="button"
      className={`flashcard ${
        showAnswer
          ? "flashcard-active"
          : ""
      }`}
      onClick={() =>
        setShowAnswer(
          (current) => !current
        )
      }
    >

      <div className="flashcard-top">

        <span>
          CARD{" "}
          {String(index + 1).padStart(
            2,
            "0"
          )}
        </span>

        <span className="flip-label">

          {showAnswer
            ? "QUESTION"
            : "REVEAL"}

        </span>

      </div>


      <div className="flashcard-content">

        {showAnswer ? (
          <>
            <small>
              ANSWER
            </small>

            <p>
              {card.answer}
            </p>
          </>
        ) : (
          <>
            <small>
              QUESTION
            </small>

            <h4>
              {card.question}
            </h4>
          </>
        )}

      </div>


      <div className="flashcard-bottom">

        {showAnswer
          ? "Click to see question"
          : "Click to reveal answer"}

      </div>

    </button>
  );
}


/* -------------------------------- */
/* Quiz */
/* -------------------------------- */

function QuizCard({
  quiz,
  index,
}: {
  quiz: QuizQuestion;
  index: number;
}) {
  const [selected, setSelected] =
    useState<string | null>(null);

  const answered =
    selected !== null;

  const isCorrect =
    selected === quiz.correct_answer;

  return (
    <div className="quiz-item">

      <div className="quiz-question">

        <span className="quiz-number">

          {String(index + 1).padStart(
            2,
            "0"
          )}

        </span>

        <h4>
          {quiz.question}
        </h4>

      </div>


      <div className="quiz-options">

        {quiz.options.map(
          (option, optionIndex) => {

            const isSelected =
              selected === option;

            const isCorrectAnswer =
              option ===
              quiz.correct_answer;

            let className =
              "quiz-option";

            if (
              answered &&
              isCorrectAnswer
            ) {
              className += " correct";
            }

            if (
              answered &&
              isSelected &&
              !isCorrectAnswer
            ) {
              className += " incorrect";
            }

            return (
              <button
                type="button"
                key={option}
                className={className}
                disabled={answered}
                onClick={() =>
                  setSelected(option)
                }
              >

                <span className="option-letter">

                  {String.fromCharCode(
                    65 + optionIndex
                  )}

                </span>

                <span>
                  {option}
                </span>

              </button>
            );
          }
        )}

      </div>


      {answered && (
        <div
          className={`quiz-feedback ${
            isCorrect
              ? "feedback-correct"
              : "feedback-incorrect"
          }`}
        >

          <div className="feedback-title">

            <strong>

              {isCorrect
                ? "Correct"
                : "Not quite"}

            </strong>

            {!isCorrect && (
              <span>

                Correct answer:{" "}
                {quiz.correct_answer}

              </span>
            )}

          </div>

          <p>
            {quiz.explanation}
          </p>

        </div>
      )}

    </div>
  );
}