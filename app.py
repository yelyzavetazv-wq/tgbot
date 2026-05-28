            elif category == 'status':
                user_choices[chat_id]['status'] = value
                choices = user_choices[chat_id]
                data = user_data.get(chat_id)
                if not data:
                    bot.send_message(chat_id, "❌ Ошибка: данные не найдены")
                    return 'OK', 200

                # Получаем ID прогресс-бара
                progress_msg_id = data.get('progress_msg_id')
                
                try:
                    if progress_msg_id:
                        bot.edit_message_text("⏳ [▓▓▓▓▓▓▓▓▓▓] 100% - Публикую...", chat_id, progress_msg_id)
                    
                    # Пост 1: обложка
                    with open(data['cover_path'], 'rb') as img:
                        bot.send_photo(CHANNEL_ID, img)
                    
                    # Пост 2: текст
                    post2 = format_text_post(data['info'], data['chapters'], choices['status'], data['annotation'])
                    bot.send_message(CHANNEL_ID, post2, parse_mode="HTML", disable_web_page_preview=True)
                    
                    # Пост 3: параметры + файлы в одном сообщении
                    post3 = format_files_post(choices['glossary'], choices['translation'], choices.get('filter', 'none'))
                    
                    # Отправляем файлы с подписью (параметры будут под последним файлом)
                    all_files = data['epub_files'] + data['fb2_files']
                    for i, file_path in enumerate(all_files):
                        with open(file_path, 'rb') as f:
                            if i == len(all_files) - 1:
                                # К последнему файлу прикрепляем подпись с параметрами
                                bot.send_document(CHANNEL_ID, f, caption=post3)
                            else:
                                bot.send_document(CHANNEL_ID, f)
                    
                    # Обновляем прогресс-бар до 100% и удаляем
                    if progress_msg_id:
                        bot.edit_message_text("✅ [▓▓▓▓▓▓▓▓▓▓] 100% - Книга опубликована!", chat_id, progress_msg_id)
                        time.sleep(1)
                        bot.delete_message(chat_id, progress_msg_id)
                    
                    bot.send_message(chat_id, f"✅ Книга '{data['info'].get('title_ru', 'Без названия')}' опубликована в канале!")
                    
                except Exception as e:
                    if progress_msg_id:
                        bot.edit_message_text(f"❌ Ошибка: {str(e)[:50]}", chat_id, progress_msg_id)
                        time.sleep(2)
                        bot.delete_message(chat_id, progress_msg_id)
                    bot.send_message(chat_id, f"❌ Ошибка публикации: {str(e)}")
                
                shutil.rmtree(data['extract_path'])
                os.remove(data['zip_path'])
                del user_choices[chat_id]
                del user_data[chat_id]
